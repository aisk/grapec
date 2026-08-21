"""A thriftpy2 server standing in for a thrift service written in any language."""

from pathlib import Path

import thriftpy2
from thriftpy2.protocol import TBinaryProtocolFactory
from thriftpy2.rpc import make_server
from thriftpy2.transport import TFramedTransportFactory

store = thriftpy2.load(str(Path(__file__).with_name("store.thrift")), module_name="store_thrift")
items: dict[str, object] = {}


class Handler:
    def get(self, key, limit):
        try:
            return items[key]
        except KeyError:
            raise store.NotFound(key=key) from None

    def put(self, item):
        items[item.key] = item

    def total(self):
        return sum(item.count for item in items.values())


if __name__ == "__main__":
    server = make_server(
        store.Store, Handler(), "127.0.0.1", 9090,
        proto_factory=TBinaryProtocolFactory(), trans_factory=TFramedTransportFactory(),
    )
    print("thrift server on 127.0.0.1:9090")
    server.serve()
