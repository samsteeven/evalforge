import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import EditorDep, WorkspaceDep, not_found
from evalforge_api.modules.datasets.models import Dataset, DatasetVersion, TestCase
from evalforge_api.modules.projects.routes import get_project
from evalforge_engine.datasets import MAX_BYTES, DatasetFormatError
from evalforge_engine.datasets import parse as parse_cases

router = APIRouter(tags=["datasets"])

MAX_CASES_PER_VERSION = 1000


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


class TestCaseIn(BaseModel):
    __test__ = False

    id: str | None = Field(default=None, min_length=1, max_length=200)
    type: Literal["qa", "rag", "agent"] = "qa"
    input: dict[str, Any] = Field(min_length=1)
    expected: dict[str, Any] | None = None
    context: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetVersionCreate(BaseModel):
    cases: list[TestCaseIn] = Field(min_length=1, max_length=MAX_CASES_PER_VERSION)

    @model_validator(mode="after")
    def assign_unique_ids(self) -> "DatasetVersionCreate":
        for position, case in enumerate(self.cases, start=1):
            case.id = case.id or f"case-{position:03d}"
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"identifiants de cas en double : {', '.join(duplicates)}")
        return self


class DatasetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    checksum: str
    case_count: int
    created_at: datetime


class TestCaseOut(BaseModel):
    __test__ = False

    id: uuid.UUID
    external_id: str
    type: str
    input: dict[str, Any]
    expected: dict[str, Any] | None
    context: list[str]
    metadata: dict[str, Any]


def checksum(cases: list[TestCaseIn]) -> str:
    canonical = json.dumps([case.model_dump(mode="json") for case in cases], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def get_dataset(session: AsyncSession, workspace_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
    )
    if dataset is None:
        raise not_found("Jeu de données")
    return dataset


async def get_dataset_version(session: AsyncSession, workspace_id: uuid.UUID, version_id: uuid.UUID) -> DatasetVersion:
    version = await session.scalar(
        select(DatasetVersion).where(DatasetVersion.id == version_id, DatasetVersion.workspace_id == workspace_id)
    )
    if version is None:
        raise not_found("Version de jeu de données")
    return version


@router.post("/projects/{project_id}/datasets", status_code=201, response_model=DatasetOut)
async def create_dataset(
    project_id: uuid.UUID, body: DatasetCreate, workspace_id: EditorDep, session: SessionDep
) -> Dataset:
    await get_project(session, workspace_id, project_id)
    dataset = Dataset(workspace_id=workspace_id, project_id=project_id, name=body.name, description=body.description)
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Un jeu de données porte déjà ce nom dans ce projet") from None
    return dataset


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut])
async def list_datasets(project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> list[Dataset]:
    await get_project(session, workspace_id, project_id)
    return list(await session.scalars(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.name)))


@router.post("/datasets/{dataset_id}/versions", status_code=201, response_model=DatasetVersionOut)
async def create_version(
    dataset_id: uuid.UUID,
    body: DatasetVersionCreate,
    response: Response,
    workspace_id: EditorDep,
    session: SessionDep,
) -> DatasetVersion:
    """Crée une version immuable. Un contenu identique à la dernière version la renvoie au lieu d'en créer une."""
    await get_dataset(session, workspace_id, dataset_id)
    digest = checksum(body.cases)
    latest = await session.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version.desc())
        .limit(1)
    )
    if latest is not None and latest.checksum == digest:
        response.status_code = 200
        return latest

    version = DatasetVersion(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        version=(latest.version + 1) if latest else 1,
        checksum=digest,
        case_count=len(body.cases),
    )
    session.add(version)
    await session.flush()
    session.add_all(
        TestCase(
            dataset_version_id=version.id,
            position=position,
            external_id=case.id,
            type=case.type,
            input=case.input,
            expected=case.expected,
            context=case.context,
            metadata_=case.metadata,
        )
        for position, case in enumerate(body.cases)
    )
    try:
        await session.commit()
    except IntegrityError:  # deux créations simultanées du même numéro de version
        raise HTTPException(409, "Une autre version vient d'être créée, réessayez") from None
    return version


@router.post("/datasets/{dataset_id}/versions/import", status_code=201, response_model=DatasetVersionOut)
async def import_version(
    dataset_id: uuid.UUID,
    file: UploadFile,
    response: Response,
    workspace_id: EditorDep,
    session: SessionDep,
) -> DatasetVersion:
    """Import d'un fichier .json ou .csv (format décrit dans evalforge_engine.datasets), mêmes règles qu'en JSON."""
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"Fichier trop gros : {MAX_BYTES // 1024 // 1024} Mo au plus")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "Encodage attendu : UTF-8") from None
    try:
        body = DatasetVersionCreate.model_validate({"cases": parse_cases(text, file.filename or "")})
    except DatasetFormatError as error:
        raise HTTPException(422, f"Fichier refusé : {error}") from None
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise HTTPException(422, f"Fichier refusé : {where} : {first['msg']}") from None
    return await create_version(dataset_id, body, response, workspace_id, session)


@router.get("/datasets/{dataset_id}/versions", response_model=list[DatasetVersionOut])
async def list_versions(dataset_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> list[DatasetVersion]:
    await get_dataset(session, workspace_id, dataset_id)
    rows = await session.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version)
    )
    return list(rows)


@router.get("/dataset-versions/{version_id}/cases", response_model=list[TestCaseOut])
async def list_cases(
    version_id: uuid.UUID,
    workspace_id: WorkspaceDep,
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[TestCaseOut]:
    await get_dataset_version(session, workspace_id, version_id)
    rows = await session.scalars(
        select(TestCase)
        .where(TestCase.dataset_version_id == version_id)
        .order_by(TestCase.position)
        .limit(limit)
        .offset(offset)
    )
    return [
        TestCaseOut(
            id=row.id,
            external_id=row.external_id,
            type=row.type,
            input=row.input,
            expected=row.expected,
            context=row.context,
            metadata=row.metadata_,
        )
        for row in rows
    ]


async def count_cases(session: AsyncSession, version_id: uuid.UUID) -> int:
    return await session.scalar(select(func.count()).where(TestCase.dataset_version_id == version_id)) or 0
