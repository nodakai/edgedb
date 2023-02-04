


from functools import singledispatch

# import pprint

from .data.data_ops import *
from .data.expr_ops import *
from .helper_funcs import *
import sys
import traceback
from edb.edgeql import ast as qlast
from edb import edgeql
from edb import errors

from edb.schema.pointers import PointerDirection
import pprint

from .shape_ops import *

from edb.common import debug, parsing
from typing import *

DEFAULT_HEAD_NAME = "__no_clash_head_subject__" 
# used as the name for the leading dot notation!, will always disappear when elaboration finishes

def elab_error(msg : str, ctx : Optional[parsing.ParserContext]) -> Any:
    raise errors.QueryError(msg, context=ctx)

def elab_not_implemented(node : qlast.Base, msg : str = "") -> Any:
    debug.dump(node)
    debug.dump_edgeql(node)
    raise ValueError("Not Implemented!", msg, node)


def elab_Shape(elements : List[qlast.ShapeElement]) -> ShapeExpr:
    """ Convert a concrete syntax shape to object expressions"""
    result = {}
    [result := {**result, name : e } 
        if name not in result.keys() 
        else (elab_error("Duplicate Value in Shapes", se.context))
    for se in elements
    for (name, e) in [elab_ShapeElement(se)]]
    return ShapeExpr(result)

@singledispatch
def elab(node: qlast.Base) -> Expr:
    return elab_not_implemented(node)

@elab.register(qlast.Path)
def elab_Path(p : qlast.Path) -> Expr:
    result : Expr = None #type: ignore[assignment]
    if p.partial:
        result = FreeVarExpr(DEFAULT_HEAD_NAME)
    for step in p.steps:
        match step:
            case qlast.ObjectRef(name=name):
                if result is None:
                    result = FreeVarExpr(var=name)
                else:
                    raise ValueError("Unexpected ObjectRef in Path")
            case qlast.Ptr(ptr=qlast.ObjectRef(name=path_name), direction=PointerDirection.Outbound):
                if result is None:
                    raise ValueError("should not be")
                else:
                    result = ProdProjExpr(result, path_name)
            case _:
                elab_not_implemented(step, "in path")
    return result


def elab_single_name(p : qlast.Path) -> str:
    """ Elaborates a single name e.g. in the left hand side of a shape """
    match p:
        case qlast.Path(steps=[qlast.Ptr(ptr=qlast.ObjectRef(name=pname), direction=s_pointers.PointerDirection.Outbound)]):
            return pname
        case qlast.Path(steps=[qlast.Ptr(ptr=qlast.ObjectRef(name=pname), type='property')]):
            return "@" + pname #TODO: handle link properties
    return elab_not_implemented(p, "single_name")


@elab.register(qlast.ShapeElement)
def elab_ShapeElement(s : qlast.ShapeElement) -> Tuple[str, BindingExpr]:
    if s.orderby or s.where:
        return elab_not_implemented(s)
    else:
        if s.compexpr is not None: 
            # l := E -> l := ¶.e if E -> e
            if s.operation.op != qlast.ShapeOp.ASSIGN:
                return elab_not_implemented(s)
            else:
                name = elab_single_name(s.expr)
                val = abstract_over_expr(elab(s.compexpr), DEFAULT_HEAD_NAME)
                return (name, val)
        elif s.elements:
            # l : S -> l := x. (x ⋅ l) s if S -> s
            name = elab_single_name(s.expr)
            return (name, BindingExpr(
                ShapedExprExpr(ProdProjExpr(BoundVarExpr(1), name),
                    elab_Shape(s.elements)
                )))
        else:
            # l -> l := x. (x ⋅ l)
            name = elab_single_name(s.expr)
            return (name, BindingExpr(ProdProjExpr(BoundVarExpr(1), name)))



@elab.register(qlast.Shape)
def elab_ShapedExpr(shape : qlast.Shape) -> ShapedExprExpr:

    return ShapedExprExpr(
            expr=elab(shape.expr) if shape.expr is not None else elab_error("Not Implemented, Emtpy Expr in Shape", shape.context), 
            shape=elab_Shape(shape.elements)
            )

@elab.register(qlast.InsertQuery)
def elab_InsertQuery(expr : qlast.InsertQuery) -> InsertExpr:
    # debug.dump(expr)
    subject_type = expr.subject.name
    object_shape = elab_Shape(expr.shape)
    object_expr = shape_to_expr(object_shape)
    return InsertExpr(name=subject_type, new=object_expr)



@elab.register(qlast.StringConstant)
def elab_StringConstant(e : qlast.StringConstant) -> StrVal: 
    return StrVal(val=e.value)

@elab.register(qlast.IntegerConstant)
def elab_IntegerConstant(e : qlast.IntegerConstant) -> IntVal: 
    abs_val = int(e.value)
    if e.is_negative:
        abs_val = -abs_val
    return IntVal(val=abs_val)

def elab_orderby(qle : List[qlast.SortExpr]) -> BindingExpr:
    result : List[Expr] = []



    for sort_expr in qle:
        if sort_expr.nones_order is not None:
            raise elab_not_implemented(sort_expr)

        elabed_expr = elab(sort_expr.path)
        if sort_expr.direction == qlast.SortOrder.Asc:
            result = [*result, elabed_expr, UnnamedTupleExpr([])]
        elif sort_expr.direction == qlast.SortOrder.Desc:
            result = [*result, UnnamedTupleExpr([]), elabed_expr]
        else: 
            raise elab_not_implemented(sort_expr, "direction not known")

    return abstract_over_expr(UnnamedTupleExpr(result), DEFAULT_HEAD_NAME)

@elab.register(qlast.SelectQuery)
def elab_SelectExpr(qle : qlast.SelectQuery) -> Expr:
    if qle.offset is not None or qle.limit is not None:
        return OffsetLimitExpr(
            subject=elab(qle.result),
            offset=elab(qle.offset) if qle.offset is not None else IntVal(0),
            limit=elab(qle.limit) if qle.limit is not None else IntInfVal(),
        )
    else:
        return FilterOrderExpr(
            subject=elab(qle.result),
            filter=abstract_over_expr(elab(qle.where), DEFAULT_HEAD_NAME) if qle.where else abstract_over_expr(BoolVal(True)),
            order=elab_orderby(qle.orderby) if qle.orderby else abstract_over_expr(UnnamedTupleExpr([])),
        )
    
@elab.register(qlast.FunctionCall)
def elab_FunctionCall(fcall : qlast.FunctionCall) -> FunAppExpr :
    if fcall.window  or fcall.kwargs:
        return elab_not_implemented(fcall)
    if type(fcall.func) is not str:
        return elab_not_implemented(fcall)
    fname = FreeVarExpr(fcall.func)
    args = [elab(arg) for arg in fcall.args]
    return FunAppExpr(fname, args)
    


@elab.register(qlast.BinOp)
def elab_BinOp(binop : qlast.BinOp) -> FunAppExpr: 
    if binop.rebalanced:
        return elab_not_implemented(binop)
    left_expr = elab(binop.left)
    right_expr = elab(binop.right)
    return FunAppExpr(fun=FreeVarExpr(binop.op), args=[left_expr, right_expr])
