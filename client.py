import grpc
import grapec

hello_pb2, hello_pb2_grpc = grapec.load('hello.proto')


def run():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = hello_pb2_grpc.GreeterStub(channel)
        req = hello_pb2.HelloRequest(name='xxx')
        print(stub.SayHello(req))


if __name__ == "__main__":
    run()
