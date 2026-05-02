from agentnexus.rag.grep_search import grep_available, grep_search
from agentnexus.rag.router import retrieve, is_code_query

print("rg available:", grep_available())
if grep_available():
    r = grep_search("def run", ".", top_k=3)
    for x in r:
        print(f"  {x['file']}:{x['line']}  {x['text'][:50]}")

print("is_code_query('config 配置'):", is_code_query("找一下 config 配置文件"))
print("is_code_query('守望先锋'):", is_code_query("守望先锋是什么"))
