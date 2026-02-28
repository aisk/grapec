import grpc
import grapec

grapec.install_import_hook()

import hello_pb2
import hello_pb2_grpc


def run():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = hello_pb2_grpc.GreeterStub(channel)
        req = hello_pb2.HelloRequest(name='Grapec')
        resp = stub.SayHello(req)
        print(resp.message)


if __name__ == "__main__":
    run()
