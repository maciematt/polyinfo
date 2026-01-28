import base64, json
 
 
def load_poly():

    poly = []

    for i in range(1, 65):
        with open(f'./data/b64_{i}.txt', 'r') as f:
            b64 = f.read().strip()
        poly.append(
            json.loads(base64.b64decode(b64).decode("utf-8"))['polymer_data']
        )

    return poly
