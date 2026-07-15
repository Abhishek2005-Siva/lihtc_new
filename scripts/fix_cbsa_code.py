"""One-time fix: strip .0 float suffix from CensusTract.cbsa_code and remove nan values.

Run once:
    cd "c:/Users/Abhishek/Documents/Graph Rag"
    PYTHONPATH=project venv/Scripts/python scripts/fix_cbsa_code.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "project"))

from neo4j import GraphDatabase
from backend.utils.config import load_settings

cfg = load_settings()
driver = GraphDatabase.driver(cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password))

with driver.session() as s:
    r = s.run("""
        MATCH (ct:CensusTract)
        WHERE ct.cbsa_code IS NOT NULL AND ct.cbsa_code ENDS WITH '.0'
        SET ct.cbsa_code = left(ct.cbsa_code, size(ct.cbsa_code)-2)
        RETURN count(ct) AS fixed
    """)
    print(f"cbsa_code .0 suffix stripped: {r.single()['fixed']} nodes")

    r2 = s.run("""
        MATCH (ct:CensusTract)
        WHERE ct.cbsa_code = 'nan'
        REMOVE ct.cbsa_code
        RETURN count(ct) AS cleaned
    """)
    print(f"cbsa_code 'nan' values removed: {r2.single()['cleaned']} nodes")

driver.close()
print("Done.")
