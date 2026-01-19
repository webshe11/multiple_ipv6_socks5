import asyncio
import socket
import struct
import subprocess
import ipaddress
import sys
import argparse
import re
import os
import platform
import ctypes
import itertools
import random

class Socks5Server:
    def __init__(self, port, bind_ip_or_list):
        self.port = port
        self.bind_ips = bind_ip_or_list if isinstance(bind_ip_or_list, list) else [bind_ip_or_list]
        self.ip_cycle = itertools.cycle(self.bind_ips)
        self.mode = "Round-Robin" if len(self.bind_ips) > 1 else "Single-IP"

    def get_next_bind_ip(self):
        return next(self.ip_cycle)

    async def handle_client(self, reader, writer):
        try:
            # 1. Handshake
            data = await reader.read(256)
            if not data or data[0] != 0x05:
                return

            writer.write(b'\x05\x00')
            await writer.drain()

            # 2. Request
            data = await reader.read(4)
            if len(data) < 4 or data[1] != 0x01: # 0x01 = CONNECT
                return

            atyp = data[3]
            dst_addr = None
            dst_port = None

            if atyp == 0x01: # IPv4
                addr_data = await reader.read(4)
                dst_addr = socket.inet_ntoa(addr_data)
            elif atyp == 0x03: # Domain
                len_byte = await reader.read(1)
                domain_len = len_byte[0]
                dst_addr = (await reader.read(domain_len)).decode()
            elif atyp == 0x04: # IPv6
                addr_data = await reader.read(16)
                dst_addr = socket.inet_ntop(socket.AF_INET6, addr_data)
            else:
                return

            port_data = await reader.read(2)
            dst_port = struct.unpack('>H', port_data)[0]

            # 3. Connect to Target
            try:
                # Force binding to our specific local ipv6
                current_bind_ip = self.get_next_bind_ip()
                local_addr = (current_bind_ip, 0, 0, 0) 
                
                remote_reader, remote_writer = await asyncio.open_connection(
                    host=dst_addr, 
                    port=dst_port,
                    local_addr=local_addr 
                )
                
                # Reply Success
                writer.write(b'\x05\x00\x00\x01' + socket.inet_aton('0.0.0.0') + struct.pack('>H', 0))
                await writer.drain()

                await asyncio.gather(
                    self.pipe(reader, remote_writer),
                    self.pipe(remote_reader, writer)
                )

            except Exception as e:
                # print(f"[{self.port}] Connection failed: {e}")
                # Reply Failure
                writer.write(b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
                await writer.drain()

        except Exception as e:
            pass
        finally:
            writer.close()

    async def pipe(self, reader, writer):
        try:
            while not reader.at_eof():
                data = await reader.read(8192)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except:
            pass
        finally:
            writer.close()

    async def start(self):
        server = await asyncio.start_server(
            self.handle_client, '127.0.0.1', self.port
        )
        if self.mode == "Round-Robin":
             print(f"[*] Proxy running on 127.0.0.1:{self.port} -> Round-Robin across {len(self.bind_ips)} IPs")
        else:
             print(f"[*] Proxy running on 127.0.0.1:{self.port} -> Outbound: {self.bind_ips[0]}")
        
        async with server:
            await server.serve_forever()

def is_admin():
    try:
        if platform.system() == 'Windows':
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except:
        return False

def get_ipv6_prefix(interface):
    """
    Attempts to retrieve the first global IPv6 address from the given interface
    and returns a tuple (prefix_address, prefix_length).
    """
    system = platform.system()
    print(f"[*] Attempting to auto-detect IPv6 prefix for {interface} on {system}...")
    
    try:
        if system == 'Windows':
            # Windows: netsh interface ipv6 show address "interface"
            cmd = f'netsh interface ipv6 show address "{interface}"'
            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            if result.returncode != 0:
                print(f"[!] Error running netsh for {interface}")
                return None, None
            
            # Output parsing for Windows
            for line in result.stdout.splitlines():
                parts = line.split()
                if not parts: continue
                # Basic heuristic: look for valid global IPv6 in the line
                for part in parts:
                    if ':' in part and not part.startswith('fe80'):
                         try:
                             clean_ip = part.split('%')[0] # Remove scope id if any
                             # Windows output usually doesn't show /CIDR in "show address"
                             # We might need to guess or default to 64 if not found, 
                             # but let's see if we can parse it if it exists.
                             if '/' in clean_ip:
                                 net = ipaddress.IPv6Interface(clean_ip)
                             else:
                                 # Fallback: try to find "Prefix Length" or similar if possible, 
                                 # but for now default to 64 if not explicit, 
                                 # or maybe the user has to provide it if non-standard.
                                 net = ipaddress.IPv6Interface(clean_ip + "/64")
                             
                             prefix = str(net.network.network_address)
                             cidr = net.network.prefixlen
                             print(f"[*] Detected Global IPv6: {clean_ip}")
                             print(f"[*] Derived Prefix: {prefix}/{cidr}")
                             return prefix, cidr
                         except ValueError:
                             pass
        else:
            # macOS / Linux
            result = subprocess.run(["ifconfig", interface], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[!] Error running ifconfig for {interface}")
                return None, None

            for line in result.stdout.splitlines():
                line = line.strip()
                if "inet6 " in line and "fe80:" not in line:
                    parts = line.split()
                    try:
                        ip_idx = parts.index("inet6") + 1
                        full_ip = parts[ip_idx]
                        full_ip = full_ip.split('%')[0]
                        
                        # Check for "prefixlen" keyword (common on BSD/macOS)
                        cidr = 64 # default
                        if "prefixlen" in parts:
                            cidr_idx = parts.index("prefixlen") + 1
                            if cidr_idx < len(parts):
                                cidr = int(parts[cidr_idx])
                        elif "/" in full_ip:
                            # Linux style: addr/cidr
                            net = ipaddress.IPv6Interface(full_ip)
                            cidr = net.network.prefixlen
                        
                        # Reconstruct interface to get network address
                        if "/" not in full_ip:
                             net = ipaddress.IPv6Interface(f"{full_ip}/{cidr}")
                        else:
                             net = ipaddress.IPv6Interface(full_ip)
                             cidr = net.network.prefixlen

                        prefix = str(net.network.network_address)
                        print(f"[*] Detected Global IPv6: {full_ip} (Prefixlen: {cidr})")
                        print(f"[*] Derived Prefix: {prefix}/{cidr}")
                        return prefix, cidr
                    except ValueError:
                        continue
    except Exception as e:
        print(f"[!] Exception during detection: {e}")
    
    return None, None

def configure_interface(interface, ips, cidr):
    system = platform.system()
    print("[-] Configuring network interface (requires admin/root)...")
    
    for ip in ips:
        print(f"    Adding {ip}/{cidr}...")
        if system == 'Windows':
            # netsh interface ipv6 add address "Interface" IP/Mask
            cmd = f'netsh interface ipv6 add address "{interface}" {ip}/{cidr}'
            ret = subprocess.run(cmd, shell=True, capture_output=True)
            if ret.returncode != 0:
                # Check if error is "already exists"
                if b"exists" not in ret.stdout and b"exists" not in ret.stderr:
                     print(f"    Failed to add {ip}. Error: {ret.stderr.decode(errors='ignore')}")
        else:
            # macOS / Linux
            cmd_check = f"ifconfig {interface} | grep {ip}"
            check = subprocess.run(cmd_check, shell=True, stdout=subprocess.DEVNULL)
            if check.returncode == 0:
                continue

            cmd_add = f"sudo ifconfig {interface} inet6 add {ip}/{cidr}"
            ret = subprocess.run(cmd_add, shell=True)
            if ret.returncode != 0:
                print(f"    Failed to add {ip}. Run with sudo?")

def cleanup_interface(interface, ips):
    system = platform.system()
    print("[-] Cleaning up network interface...")
    
    for ip in ips:
        print(f"    Removing {ip}...")
        if system == 'Windows':
             cmd = f'netsh interface ipv6 delete address "{interface}" {ip}'
             subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # macOS
            cmd_del = f"sudo ifconfig {interface} inet6 delete {ip}"
            subprocess.run(cmd_del, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def main():
    parser = argparse.ArgumentParser(description="Multi-IPv6 SOCKS5 Proxy Generator")
    parser.add_argument("-i", "--interface", default="en0", help="Network interface (default: en0, use proper name on Windows e.g. 'Wi-Fi')")
    parser.add_argument("-p", "--prefix", help="IPv6 Prefix (e.g., 2001:db8::). Auto-detected if not set.")
    parser.add_argument("-m", "--mask", type=int, help="IPv6 Prefix Length / Mask (e.g., 64). Auto-detected if not set.")
    parser.add_argument("-P", "--start-port", type=int, default=10801, help="Starting SOCKS5 port (default: 10801). Ignored if --round-robin-port is used.")
    parser.add_argument("-c", "--count", type=int, default=5, help="Number of IPs/Ports to generate (default: 5)")
    parser.add_argument("-s", "--start-index", type=int, default=1, help="Starting IP suffix index (default: 1). If --random is used (default), this acts as a seed.")
    parser.add_argument("-rr", "--round-robin-port", type=int, help="If specified, listen on this single port and round-robin outbound traffic across all generated IPs.")
    parser.add_argument("--sequential", action="store_true", help="Generate sequential IP suffixes instead of random ones.")
    parser.add_argument("--random", action="store_true", default=True, help="Generate random IP suffixes (default).")
    parser.add_argument("--clean", action="store_true", help="Remove the generated IPs from the interface and exit")
    
    args = parser.parse_args()

    # If both are specified, sequential takes precedence or we just use the logic below
    is_random = args.random and not args.sequential

    ipv6_prefix = args.prefix
    ipv6_mask = args.mask

    # Auto-detect if missing
    if not ipv6_prefix or not ipv6_mask:
        detected_prefix, detected_mask = get_ipv6_prefix(args.interface)
        if not ipv6_prefix and detected_prefix:
            ipv6_prefix = detected_prefix
        if not ipv6_mask and detected_mask:
            ipv6_mask = detected_mask
    
    # Defaults/Fallbacks
    if not ipv6_prefix:
        print("[!] Could not auto-detect IPv6 prefix. Please provide one using --prefix.")
        sys.exit(1)
    if not ipv6_mask:
        print("[!] Could not auto-detect IPv6 mask. Defaulting to 64.")
        ipv6_mask = 64

    try:
        # Validate and Construct Network
        full_prefix_str = f"{ipv6_prefix}/{ipv6_mask}"
        net = ipaddress.IPv6Network(full_prefix_str, strict=False)
        base_int = int(net.network_address)
    except ValueError as e:
        print(f"[!] Invalid IPv6 prefix/mask configuration: {e}")
        sys.exit(1)

    if not is_admin():
        print("WARNING: This script usually requires root/admin to add IP addresses.")

    # Generate IPs
    generated_ips = []
    if is_random:
        # Seed with start-index to allow reproducible "random" sets for cleanup
        random.seed(args.start_index)
        used_suffixes = set()
        max_suffix = (1 << (128 - ipv6_mask)) - 1
        while len(generated_ips) < args.count:
            suffix = random.randint(1, max_suffix)
            if suffix not in used_suffixes:
                used_suffixes.add(suffix)
                new_ip_int = base_int + suffix
                new_ip = ipaddress.IPv6Address(new_ip_int)
                generated_ips.append(str(new_ip))
    else:
        for i in range(args.count):
            suffix = args.start_index + i
            new_ip_int = base_int + suffix
            new_ip = ipaddress.IPv6Address(new_ip_int)
            generated_ips.append(str(new_ip))

    # --- CONFIGURATION SUMMARY & CONFIRMATION ---
    print("\n" + "="*40)
    print("      MULTI-IPv6 SOCKS5 CONFIGURATION      ")
    print("="*40)
    print(f" Interface    : {args.interface}")
    print(f" IPv6 Prefix  : {ipv6_prefix}")
    print(f" Subnet Mask  : /{ipv6_mask}")
    print(f" IP Count     : {args.count}")
    print(f" Start Index  : {args.start_index}")
    
    if args.clean:
        print(f" Mode         : CLEANUP (Remove IPs)")
    else:
        print(f" Mode         : SETUP (Add IPs & Start Proxies)")
        if args.round_robin_port:
             print(f" Proxy Type   : Single Port ({args.round_robin_port}) -> Round Robin")
        else:
             print(f" Proxy Type   : Multi-Port (Starting {args.start_port}) -> 1 IP per Port")
        
        print("-" * 40)
        print(" Generated IPs Preview:")
        for i in range(min(3, len(generated_ips))):
             print(f"  - {generated_ips[i]}")
        if len(generated_ips) > 3:
            print(f"  ... and {len(generated_ips)-3} more")

    print("="*40)
    
    # Confirmation Loop
    while True:
        try:
            choice = input("\nProceed with this configuration? [y/N]: ").strip().lower()
            if choice == 'y':
                break
            elif choice == 'n' or choice == '':
                print("[-] Aborted by user.")
                sys.exit(0)
        except EOFError:
             sys.exit(0)

    if args.clean:
        cleanup_interface(args.interface, generated_ips)
        return

    # Configure Interface
    configure_interface(args.interface, generated_ips, ipv6_mask)

    # Start Servers
    if args.round_robin_port:
        # Single server, round-robin mode
        server = Socks5Server(args.round_robin_port, generated_ips)
        print("\n[+] Starting Single Round-Robin Proxy...")
        await server.start()
    else:
        # Multiple servers, one per IP
        tasks = []
        for i, ip in enumerate(generated_ips):
            port = args.start_port + i
            server = Socks5Server(port, ip)
            tasks.append(asyncio.create_task(server.start()))

        print("\n[+] All proxies started. Press Ctrl+C to stop.")
        await asyncio.gather(*tasks)

if __name__ == '__main__':
    try:
        if platform.system() == 'Windows':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping...")