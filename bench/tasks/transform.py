users = [
    {"name": "ann", "age": 28},
    {"name": "bob", "age": 45},
    {"name": "cid", "age": 33},
]
print([u["name"] for u in users if u["age"] > 30])
