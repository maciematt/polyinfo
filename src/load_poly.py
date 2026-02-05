import base64, json, pickle
 
 
def load_poly_lvl1():

    poly = []

    for i in range(1, 65):
        with open(f'../data/lvl1_b64/b64_{i}.txt', 'r') as f:
            b64 = f.read().strip()
        poly.append(
            json.loads(base64.b64decode(b64).decode("utf-8"))['polymer_data']
        )

    return poly


def load_poly_lvl2():
    with open('../data/polymer_details.pkl', 'rb') as f:
        lvl2_data = pickle.load(f)

    return [json.loads(base64.b64decode(_["json"]).decode("utf-8")) for _ in lvl2_data.values()]
