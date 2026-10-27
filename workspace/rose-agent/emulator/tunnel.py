"""Emulator-only TCP multiplexer over SSH stdio. TLS remains end to end."""
import asyncio, struct, sys
HEADER=struct.Struct('!BII')
async def main():
 remote=len(sys.argv)>1 and sys.argv[1]=='remote'
 if remote:
  incoming=asyncio.StreamReader();protocol=asyncio.StreamReaderProtocol(incoming)
  await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin.buffer)
  async def emit(data):sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
 else:
  proc=await asyncio.create_subprocess_exec('ssh','-T','quantus-rtx','MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -- python3 /home/pixelbuild/pixel-os/emulator-tunnel.py remote',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
  incoming=proc.stdout
  async def emit(data):proc.stdin.write(data);await proc.stdin.drain()
 peers={};counter=0;lock=asyncio.Lock()
 async def send(kind,ident,data=b''):
  async with lock:await emit(HEADER.pack(kind,ident,len(data))+data)
 async def read_peer(ident,reader,writer):
  try:
   while data:=await reader.read(32768):await send(2,ident,data)
  except (OSError,ConnectionError):pass
  finally:
   peers.pop(ident,None);writer.close();await send(3,ident)
 async def accept(reader,writer):
  nonlocal counter
  counter+=1;ident=counter;peers[ident]=writer
  await send(1,ident);asyncio.create_task(read_peer(ident,reader,writer))
 server=await asyncio.start_server(accept,'127.0.0.1',19444) if remote else None
 if remote:print('WSL_LOOPBACK_19444_READY',file=sys.stderr,flush=True)
 try:
  while True:
   kind,ident,size=HEADER.unpack(await incoming.readexactly(HEADER.size))
   if size>32768:raise ValueError('Invalid frame')
   data=await incoming.readexactly(size)
   if kind==1 and not remote:
    try:
     reader,writer=await asyncio.open_connection('127.0.0.1',19444);peers[ident]=writer
     asyncio.create_task(read_peer(ident,reader,writer))
    except OSError:await send(3,ident)
   elif kind==2 and ident in peers:
    try:peers[ident].write(data);await peers[ident].drain()
    except (OSError,ConnectionError):
     writer=peers.pop(ident,None)
     if writer:writer.close()
     await send(3,ident)
   elif kind==3:
    writer=peers.pop(ident,None)
    if writer:writer.close()
 except asyncio.IncompleteReadError:pass
 finally:
  for writer in peers.values():writer.close()
  if server:server.close();await server.wait_closed()
  if not remote:proc.terminate()
asyncio.run(main())
