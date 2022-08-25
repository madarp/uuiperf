#!/usr/bin/env python3
"""
uui_perf - A long-running program to collect DNS query times and network ping
statistics over time.
"""
import sys
import re
import logging
from datetime import datetime as dt
import time

import asyncio
import argparse
from timeit import default_timer as timer
import subprocess
import csv
import signal

import dns.message
import dns.asyncquery
import dns.asyncresolver
import dns.exception


logger = logging.getLogger(__name__)
exit_flag = False

dns_servers = {
    "Cisco OpenDNS": ("208.67.222.222", "208.67.220.220"),
    "Cloudflare":    ("1.1.1.1", "1.0.0.1"),
    "Google":        ("8.8.8.8", "8.8.4.4"),
    "Quad9":         ("149.112.112.112",),
}

ping_servers = [
    "dns.google.com",
    "yahoo.com",
    "ping.ubnt.com"
]


def get_ping_latency_ms():
    """Sends a ping request to a handful of servers, returns avg response time"""
    times = []
    for p in ping_servers:
        command = ['ping', '-c', '1', '-W', '1', p]
        try:
            response = subprocess.check_output(command).decode()
            reading = re.search(r'time=?([0-9]*\.[0-9]+) ms', response)
            if reading:
                ms = float(reading.group(1))
                times.append(ms)
        except subprocess.CalledProcessError:
            # could not contact the ping server
            logger.error(f"ping timeout on {p}")
            continue

    return sum(times)/len(times)


async def get_dns_latency_ms():
    """Query a set of DNS providers, get some average lookup times over tcp, udp, tls"""
    dns_name = 'uui.org'
    times = []
    for name, servers in dns_servers.items():
        for s in servers:
            q = dns.message.make_query(dns_name, "A")

            try:
                # start = timer()
                # r = await dns.asyncquery.udp(q, s)
                # udp_seconds = timer() - start
                # logger.debug(f"{name}:\t{s}  {udp_seconds}")
                # times.append(udp_seconds)

                start = timer()
                await dns.asyncquery.tcp(q, s, timeout=1.0)
                tcp_seconds = timer() - start
                logger.debug(f"{name}:\t{s}  {tcp_seconds}")
                times.append(tcp_seconds)

                # start = timer()
                # r = await dns.asyncquery.tls(q, s, timeout=1.0)
                # tls_seconds = timer() - start
                # logger.debug(f"{name}:\t{s}  {tls_seconds}")
                # times.append(tls_seconds)

                # start = timer()
                # r = await dns.asyncresolver.resolve(dns_name, "A")
                # resolve_seconds = timer() - start
                # logger.debug(f"{name}:\t{s}  {resolve_seconds}")
                # times.append(resolve_seconds)

            except dns.exception.Timeout:
                logger.error(f"dns timeout on {name}: {s}")
                times.append(0.0)
                continue

    try:
        start = timer()
        await dns.asyncresolver.zone_for_name(dns_name)
        zone_time = timer() - start
        times.append(zone_time)
    except dns.resolver.LifetimeTimeout:
        logger.error(f"resolver timeout for {dns_name}")
        times.append(0.0)

    avg_dns_seconds = sum(times)/len(times)
    return avg_dns_seconds * 1000


async def collect_readings(interval, csvfile):
    while not exit_flag:
        dns_latency_ms = await get_dns_latency_ms()
        latency_ms = get_ping_latency_ms()
        logger.info(f"dns_latency_ms: {dns_latency_ms:.4f}, ping_latency_ms: {latency_ms:.4f}")

        with open(csvfile, 'a') as f:
            csvwriter = csv.writer(f)
            csvwriter.writerow((dt.now(), f"{dns_latency_ms:.4f}", f"{latency_ms:.4f}"))
        time.sleep(interval)


def create_parser():
    """Returns a command line argument parser."""
    parser = argparse.ArgumentParser(
        description='Watches a directory of text files for a magic string')
    parser.add_argument('-l', '--level', type=str, default='INFO',
                        help='Logging level 0-50')
    parser.add_argument('-i', '--interval', type=float,
                        default=1.0, help='Number of seconds between polling')
    parser.add_argument('-p', '--csvpath', type=str, default='uuiperf.csv',
                        help='csv file path, default is uuiperf.csv')
    return parser


def signal_handler(sig_num, frame):
    """
    This is a handler for SIGTERM and SIGINT. It just sets a flag,
    and main() will exit its loop when the signal is trapped.
    """
    global exit_flag
    logger.warning(f'Received OS process signal {signal.Signals(sig_num).name}')
    exit_flag = True


async def main(args):

    parser = create_parser()
    args = parser.parse_args(args)

    # Take a time measurement of when we started watching
    app_start_time = dt.now()

    # For now, set up just to log to console
    logging.basicConfig(
        stream=sys.stdout,
        format='%(asctime)s.%(msecs)03d %(name)-12s \
                %(levelname)-8s [%(threadName)-12s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger.setLevel(args.level)

    # A bold startup banner that is easy to see when quickly
    # scrolling through a log file
    logger.info(
        '\n'
        '-------------------------------------------------------------------\n'
        f'    Running {__file__}\n'
        f'    Started on {app_start_time.isoformat()}\n'
        '-------------------------------------------------------------------\n'
    )

    csvfields = ['Local_Date', 'DNS_Latency_ms', 'PING_Latency_ms']
    with open(args.csvpath, 'w') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow(csvfields)

    csv_line_length = 42
    kb_per_day = 86400 / args.interval * csv_line_length / 1024

    logger.info(f"CSV size estimate: {kb_per_day} Kb per day")

    # Main loop, keep alive forever unless we receive a SIGTERM, SIGINT
    while not exit_flag:
        try:
            await collect_readings(args.interval, args.csvpath)
        except KeyboardInterrupt:
            break
        except Exception as e:
            error_str = f'Unhandled Exception in MAIN\n{str(e)}\
                          \nRestarting ...'
            logger.error(error_str, exc_info=True)
            time.sleep(5.0)
            continue

    # Alas, we are dying a graceful death
    uptime = dt.now() - app_start_time
    logger.info(
        '\n'
        '-------------------------------------------------------------------\n'
        f'   Stopped {__file__}\n'
        f'   Uptime was {str(uptime)}\n'
        '-------------------------------------------------------------------\n'
    )

    logging.shutdown()
    return 0


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
