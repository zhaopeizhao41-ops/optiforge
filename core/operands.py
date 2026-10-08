"""Merit-function operand evaluation shared by the analysis and modeling tools."""


def _operand(session, code: str, *params) -> float:
    """Evaluate one merit-function operand without adding it to the merit function editor.

    The only ZOS-API overload is::

        GetOperandValue(MeritOperandType type, Int32 srf, Int32 wave,
                        Double hx, Double hy, Double px, Double py, Double ex, Double ey)

    `params` are the operand's numeric arguments, zero-padded to the eight slots that
    signature expects. pythonnet matches overloads by exact CLR type and will not
    implicitly narrow a Python float to Int32, so the two integer slots are coerced
    explicitly: passing `0.0` for `wave` used to raise "No method matches given
    arguments" and silently degrade every caller that did so (edge thickness, sag).
    """
    op_type = getattr(session.ZOSAPI.Editors.MFE.MeritOperandType, code)
    args = list(params) + [0] * (8 - len(params))
    coerced = [int(args[0]), int(args[1])] + [float(value) for value in args[2:8]]
    return float(session.system.MFE.GetOperandValue(op_type, *coerced))
