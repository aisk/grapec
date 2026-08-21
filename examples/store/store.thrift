// The IDL of the foreign thrift service. grapec does not read it, server.py does.
namespace py store

exception NotFound {
  1: string key,
}

exception Locked {
  1: string key,
}

struct Item {
  1: string key,
  2: i32 count,
  3: list<string> tags,
  5: optional string note,
}

service Store {
  Item get(1: string key, 2: i32 limit) throws (1: NotFound nf),
  void put(1: Item item) throws (1: Locked locked),
  // removed field 1 `force`, so the remaining parameter keeps its id
  i32 remove(2: string key) throws (1: NotFound nf),
  i64 total(),
}
