"""Configure the isolated project PostgreSQL instance without printing credentials."""
from pathlib import Path
from urllib.parse import quote
import psycopg

root=Path(__file__).resolve().parents[2]
password=(root/'data/postgres-local/bootstrap-password').read_text(encoding='utf-8')
with psycopg.connect(host='127.0.0.1',port=55432,dbname='postgres',user='quantpilot',password=password,autocommit=True) as connection:
    exists=connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',('quantpilot',)).fetchone()
    if not exists:connection.execute('CREATE DATABASE quantpilot')
url='postgresql+psycopg://quantpilot:'+quote(password,safe='')+'@127.0.0.1:55432/quantpilot'
docker_url='postgresql+psycopg://quantpilot:'+quote(password,safe='')+'@db:5432/quantpilot'
env=root/'.env'
if env.exists() and not (root/'data/postgres-local/previous.env').exists():
    (root/'data/postgres-local/previous.env').write_bytes(env.read_bytes())
env.write_text('QUANTPILOT_DATA_DIR=data\nQUANTPILOT_ARTIFACT_DIR=artifacts\n'
    +'QUANTPILOT_DATABASE_URL='+url+'\nQUANTPILOT_MARKET_DATABASE_URL='+url+'\n'
    +'QUANTPILOT_PG_PASSWORD='+password+'\nQUANTPILOT_DOCKER_DATABASE_URL='+docker_url+'\n'
    +'QUANTPILOT_CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]\n',encoding='utf-8')
print('Private .env configured for project PostgreSQL on localhost:55432; credentials are not displayed.')
