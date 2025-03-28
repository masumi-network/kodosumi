from kodosumi import dtypes

def _getargs(*args):
    if len(args) == 1:
        if isinstance(args[0], str):
            return args[0]
        elif isinstance(args[0], list):
            return "\n".join(str(a) for a in args[0])
        else:
            raise ValueError(f"Invalid argument type: {type(args[0])}")
    return "\n".join(str(a) for a in args)

def Markdown(*args):
    return dtypes.Markdown(body=_getargs(*args))

def HTML(*args):
    return dtypes.HTML(body=_getargs(*args))

def Text(*args):
    return dtypes.Text(body=_getargs(*args))
