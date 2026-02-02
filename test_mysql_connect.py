import asyncio
import ipaddress

# 配置参数
TARGET_NETWORK = "127.0.0.1/24"
PORT = 3306
TIMEOUT = 4.0        # TCP 连接超时
READ_TIMEOUT = 3.0   # 等待 MySQL 握手包超时
CONCURRENCY = 50     # 降低并发以提高稳定性

async def check_mysql_port(ip, semaphore):
    """尝试连接并验证是否为真正的 MySQL 服务"""
    async with semaphore:
        try:
            # 1. 尝试建立 TCP 连接
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(str(ip), PORT),
                timeout=TIMEOUT
            )
            
            is_mysql = False
            try:
                # 2. 深度验证：读取 MySQL 初始握手包
                data = await asyncio.wait_for(reader.read(64), timeout=READ_TIMEOUT)
                
                # MySQL 握手包特征：第 5 个字节是协议版本 0x0a
                if len(data) >= 5 and data[4] == 0x0a:
                    print(f"[+] 发现 MySQL 服务: {ip}")
                    is_mysql = True
                elif len(data) >= 5 and data[4] == 0xff:
                    # 0xff 开头通常是 MySQL 的错误包（比如 Too many connections 或 IP blocked）
                    # 这也证明了它是 MySQL 服务
                    print(f"[!] 发现 MySQL 服务 (但返回错误包): {ip}")
                    is_mysql = True
            except Exception:
                # 读取超时，可能是假开放端口
                pass

            writer.close()
            await writer.wait_closed()
            return str(ip) if is_mysql else None
            
        except Exception:
            # 连接失败或被拒绝
            return None

async def main():
    network = ipaddress.ip_network(TARGET_NETWORK)
    ips = list(network.hosts())
    
    print(f"开始扫描网络: {TARGET_NETWORK} (共 {len(ips)} 个 IP)")
    print(f"并发数: {CONCURRENCY}, 超时设置: 连接{TIMEOUT}s / 读取{READ_TIMEOUT}s\n")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [check_mysql_port(ip, semaphore) for ip in ips]
    
    results = await asyncio.gather(*tasks)
    
    open_ips = [res for res in results if res]
    print(f"\n扫描完成！")
    print(f"总计探测: {len(ips)} 个 IP")
    print(f"真实开放 MySQL 的 IP 数量: {len(open_ips)}")
    if open_ips:
        print("开放列表:")
        for ip in open_ips:
            print(f" - {ip}")

if __name__ == "__main__":
    asyncio.run(main())
