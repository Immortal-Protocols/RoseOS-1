"""Transparent TLS relay with a conservative TCP segment size for mobile paths.
TLS and bearer authentication remain end-to-end with the original server.
"""
import asyncio, socket

async def relay(reader, writer, fragment=False):
    try:
        while data := await reader.read(65536):
            if fragment:
                for start in range(0, len(data), 512):
                    writer.write(data[start:start+512])
                    await writer.drain()
                    await asyncio.sleep(0.01)
            else:
                writer.write(data)
                await writer.drain()
    finally:
        writer.close()

async def accept(reader, writer):
    tasks = []
    upstream = None
    try:
        sock = writer.get_extra_info('socket')
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1000)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        remote, upstream = await asyncio.open_connection('127.0.0.1', 19443)
        tasks = [asyncio.create_task(relay(reader, upstream)), asyncio.create_task(relay(remote, writer, True))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, ConnectionError) as exc:
        print('relay error:', type(exc).__name__, str(exc), flush=True)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        if upstream:
            upstream.close()

async def main():
    server = await asyncio.start_server(accept, '0.0.0.0', 9443)
    print('TLS relay ready on 9443; upstream loopback 19443; TCP MSS 1000', flush=True)
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
