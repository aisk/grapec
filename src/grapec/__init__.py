import sys
import types

from grpc_tools import protoc
from grpc_tools import _protoc_compiler


def load(path: str) -> tuple[types.ModuleType, types.ModuleType]:
    pb2_module_name = protoc._proto_file_to_module_name('_pb2', path)
    pb2_grpc_module_name = protoc._proto_file_to_module_name('_pb2_grpc', path)

    pb2_module = types.ModuleType(pb2_module_name)
    protos = _protoc_compiler.get_protos(path.encode(), [b'.'])
    exec(protos[0][1], pb2_module.__dict__)

    pb2_grpc_module = types.ModuleType(pb2_grpc_module_name)
    services = _protoc_compiler.get_services(path.encode(), [b'.'])
    previous_pb2 = sys.modules.get(pb2_module_name)
    sys.modules[pb2_module_name] = pb2_module
    try:
        exec(services[0][1], pb2_grpc_module.__dict__)
    finally:
        if previous_pb2 is None:
            del sys.modules[pb2_module_name]
        else:
            sys.modules[pb2_module_name] = previous_pb2

    return pb2_module, pb2_grpc_module
