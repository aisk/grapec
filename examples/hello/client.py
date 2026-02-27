from pathlib import Path

import grpc
import grapec

PROTO_PATH = Path(__file__).resolve().with_name("hello.proto")
hello_pb2, hello_pb2_grpc = grapec.load(str(PROTO_PATH))


def run():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = hello_pb2_grpc.GreeterStub(channel)
        req = hello_pb2.HelloRequest(name='Grapec')
        resp = stub.SayHello(req)
        print(resp.message)


if __name__ == "__main__":
    run()
