"""Absolute-path stdio entry for clients that cannot set a working directory."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luckyj.mcp import serve_mcp
p=argparse.ArgumentParser()
p.add_argument('--data',type=Path,required=True)
p.add_argument('--good-budget',type=int,default=12000)
a=p.parse_args()
serve_mcp(a.data,good_budget=a.good_budget)
