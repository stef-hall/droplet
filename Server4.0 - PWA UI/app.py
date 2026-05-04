from __future__ import annotations

import ast
import operator as op
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

_ALLOWED_OPERATORS: dict[type[ast.AST], callable] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
    ast.FloorDiv: op.floordiv,
}


def safe_eval(expression: str) -> float | int:
    """Safely evaluate simple arithmetic expressions."""
    node = ast.parse(expression, mode="eval")

    def _eval(current: ast.AST):
        if isinstance(current, ast.Expression):
            return _eval(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return current.value
        if isinstance(current, ast.BinOp) and type(current.op) in _ALLOWED_OPERATORS:
            left = _eval(current.left)
            right = _eval(current.right)
            return _ALLOWED_OPERATORS[type(current.op)](left, right)
        if isinstance(current, ast.UnaryOp) and type(current.op) in _ALLOWED_OPERATORS:
            operand = _eval(current.operand)
            return _ALLOWED_OPERATORS[type(current.op)](operand)
        raise ValueError("Only basic arithmetic is allowed.")

    return _eval(node)


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/calculate")
def calculate():
    payload = request.get_json(silent=True) or {}
    expression = str(payload.get("expression", "")).strip()

    if not expression:
        return jsonify({"ok": False, "error": "Expression is required."}), 400

    try:
        result = safe_eval(expression)
        return jsonify({"ok": True, "result": result})
    except ZeroDivisionError:
        return jsonify({"ok": False, "error": "Cannot divide by zero."}), 400
    except Exception:
        return jsonify({"ok": False, "error": "Invalid expression."}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
