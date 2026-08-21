namespace py oracle

enum Color {
  UNSPECIFIED = 0,
  RED = 1,
  BLUE = 2,
}

struct Inner {
  1: string label,
  2: i64 weight,
}

struct Everything {
  1: i64 i,
  2: byte n8,
  3: i16 n16,
  4: i32 n32,
  5: double f,
  6: string s,
  7: binary b,
  8: bool flag,
  9: Color color,
  10: Inner inner,
  11: list<i32> ints,
  12: list<string> strs,
  13: list<Inner> inners,
  14: map<string, i64> counts,
  15: map<i32, Inner> by_id,
  16: optional i64 opt_i,
  17: optional string opt_s,
  18: optional Inner opt_inner,
}

struct Pinned {
  1: i64 a,
  10: i64 b,
  11: i64 c,
}

union Choice {
  1: i64 choice_int,
  2: string choice_str,
  3: Inner choice_inner,
}

struct WithUnion {
  1: Choice choice,
}

struct Sparse {
  1: optional i64 only,
}

struct Sets {
  1: set<string> tags,
}
