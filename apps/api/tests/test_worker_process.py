"""Le worker tourne dans son propre processus, sans l'API : il doit charger tout le modèle de données lui-même.

Dans les autres tests, l'API et le worker partagent le processus, ce qui masquait un worker qui ne connaissait pas
toutes les tables (clé étrangère irrésoluble à la première écriture d'un run).
"""

import subprocess
import sys


def test_le_worker_seul_connait_toutes_les_tables():
    code = (
        "import evalforge_api.worker.main\n"
        "from evalforge_api.core.database import Base\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "configure_mappers()\n"
        "for table in Base.metadata.sorted_tables:\n"
        "    for fk in table.foreign_keys:\n"
        "        fk.column\n"  # lève NoReferencedTableError si la table visée n'est pas chargée
        "print(len(Base.metadata.tables))\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) >= 15
