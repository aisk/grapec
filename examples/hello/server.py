from pathlib import Path

import grpc
import logging
import grapec
from concurrent import futures

PROTO_PATH = Path(__file__).resolve().with_name("hello.proto")
hello_pb2, hello_pb2_grpc = grapec.load(str(PROTO_PATH))


class GreeterServicer(hello_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        if not request.name.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "name is required")
        return hello_pb2.HelloReply(message=f"Hello, {request.name}")


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    hello_pb2_grpc.add_GreeterServicer_to_server(
        GreeterServicer(), server
    )
    server.add_insecure_port("localhost:50051")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
