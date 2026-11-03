import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s - %(message)s')

# Read the search_amazon function to understand what's happening
from amazon_source import search_amazon

import amazon_source
import inspect
src = inspect.getsource(amazon_source.search_amazon)
print("search_amazon source (first 80 lines):")
for i, line in enumerate(src.split('\n')[:80]):
    print(f"  {i+1}: {line}")
