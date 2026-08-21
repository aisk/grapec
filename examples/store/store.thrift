// The IDL of the foreign thrift service. grapec does not read it, server.py does.
namespace py store

exception NotFound {
  1: string key,
}

struct Item {
  1: string key,
  2: i32 count,
  3: list<string> tags,
}

service Store {
  Item get(1: string key, 2: i32 limit) throws (1: NotFound nf),
  void put(1: Item item),
  i64 total(),
}
