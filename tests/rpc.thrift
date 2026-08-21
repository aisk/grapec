namespace py rpc

exception NotFound {
  1: string key,
}

exception Busy {
  1: i32 retry_after,
}

struct Item {
  1: string key,
  2: i32 count,
  3: list<string> tags,
}

service Store {
  Item get(1: string key, 2: i32 limit) throws (1: NotFound nf, 2: Busy busy),
  void put(1: Item item),
  i64 total(),
  map<string, i32> counts(1: list<string> keys),
  Item slow(),
  void boom(),
  Item undeclared(1: string key) throws (1: NotFound nf),
  i32 echo_opt(1: optional i32 n),
  Item pinned(1: string key) throws (1: NotFound nf, 5: Busy busy),
}
