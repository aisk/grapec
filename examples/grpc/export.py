"""Render the proto3 definition of models.py, compare it with hello.proto."""

import grapec
from models import Greeter, Stats

print(grapec.export_proto(Greeter, Stats))
