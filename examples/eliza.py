"""Talk to a public gRPC service over TLS, nothing to install or start.

demo.connectrpc.com hosts ELIZA, the 1960s chatbot. Its service is defined at
https://buf.build/connectrpc/eliza, this file is the whole of what grapec needs.
"""

import grapec


@grapec.struct(package="connectrpc.eliza.v1")
class SayRequest:
    sentence: str


@grapec.struct(package="connectrpc.eliza.v1")
class SayResponse:
    sentence: str


class ElizaService(grapec.Client, package="connectrpc.eliza.v1"):
    @grapec.name("Say")
    def say(self, request: SayRequest) -> SayResponse: ...


eliza = ElizaService("grpcs://demo.connectrpc.com", timeout=10)
for sentence in ["Hello", "I feel tired today", "My code never compiles"]:
    print("you:  ", sentence)
    print("eliza:", eliza.say(SayRequest(sentence=sentence)).sentence)
grapec.close(eliza)
