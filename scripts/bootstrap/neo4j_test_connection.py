from neo4j import GraphDatabase

URI = "neo4j://127.0.0.1:7687"
USERNAME = "neo4j"
PASSWORD = "Letmein2272"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

try:
    driver.verify_connectivity()
    print("✅ Connected to Neo4j successfully")

    with driver.session() as session:
        result = session.run("RETURN 'Neo4j is working' AS msg")
        print(result.single()["msg"])

except Exception as e:
    print("❌ Connection failed")
    print(e)

finally:
    driver.close()