import types

from grpc_tools import protoc
from grpc_tools import _protoc_compiler


def load(path: str) -> types.ModuleType:
    module_name = protoc._proto_file_to_module_name('_pb2', path)
    module = types.ModuleType(module_name)

    protos = _protoc_compiler.get_protos(path.encode(), [b'.'])
    exec(protos[0][1], module.__dict__)

    services = _protoc_compiler.get_services(path.encode(), [b'.'])
    exec(services[0][1], module.__dict__)

    return module
