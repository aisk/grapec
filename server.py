import grpc
import logging
import grapec
from concurrent import futures

hello_pb2, hello_pb2_grpc = grapec.load('hello.proto')


class GreeterServicer(hello_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        return hello_pb2.HelloRequest(name='what')


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
