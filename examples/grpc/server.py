"""A plain grpcio server standing in for a service written in any language.

Requires: pip install grpcio grpcio-tools
"""

import sys
import tempfile
from concurrent import futures
from pathlib import Path

import grpc
from grpc_tools import protoc

HERE = Path(__file__).parent
out = tempfile.mkdtemp()
protoc.main([
    "protoc",
    f"-I{HERE}",
    f"-I{Path(protoc.__file__).parent / '_proto'}",
    f"--python_out={out}",
    f"--grpc_python_out={out}",
    str(HERE / "hello.proto"),
])
sys.path.insert(0, out)

import hello_pb2  # noqa: E402
import hello_pb2_grpc  # noqa: E402


greetings = 0


class Greeter(hello_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        global greetings
        if not request.name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "name is required")
        greetings += 1
        context.set_trailing_metadata((("x-request-id", f"req-{greetings}"),))
        tags = ", ".join(f"{t.key}={t.value}" for t in request.tags)
        reply = hello_pb2.HelloReply(message=f"Hello {request.name} ({tags})")
        reply.extra["priority"] = hello_pb2.Priority.Name(request.priority)
        reply.extra["trace_id"] = request.trace_id
        if request.HasField("sent_at"):
            reply.extra["sent_at"] = request.sent_at.ToDatetime().isoformat()
        if request.WhichOneof("payload"):
            reply.extra["payload"] = str(getattr(request, request.WhichOneof("payload"))).strip()
        for key, value in context.invocation_metadata():
            if key == "x-tenant":
                reply.extra["tenant"] = value
        return reply


class Stats(hello_pb2_grpc.StatsServicer):
    def Count(self, request, context):
        return hello_pb2.CountReply(greetings=greetings)


server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
hello_pb2_grpc.add_GreeterServicer_to_server(Greeter(), server)
hello_pb2_grpc.add_StatsServicer_to_server(Stats(), server)
server.add_insecure_port("localhost:50051")
server.start()
print("listening on localhost:50051")
server.wait_for_termination()
