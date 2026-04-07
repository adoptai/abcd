import json
import sys


def main(input_data: dict) -> dict:
    return {"result": "ok"}


if __name__ == "__main__":
    input_data = json.load(sys.stdin)
    output = main(input_data)
    print(json.dumps(output))
