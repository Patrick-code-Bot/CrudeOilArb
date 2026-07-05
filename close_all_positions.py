#!/usr/bin/env python3
"""
Emergency script to close all open positions on Bybit.
Use this to clean up positions before restarting the strategy.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

if not API_KEY or not API_SECRET:
    print("ERROR: BYBIT_API_KEY or BYBIT_API_SECRET not found in .env file")
    sys.exit(1)


def main():
    """Close all open positions on Bybit."""

    print("=" * 80)
    print("BYBIT POSITION CLOSER - Emergency Position Cleanup")
    print("=" * 80)
    print()
    print("⚠️  WARNING: This will close ALL open BZ and CL positions!")
    print("⚠️  Market orders will be used for immediate execution.")
    print()

    # Ask for confirmation
    response = input("Do you want to continue? (yes/no): ").strip().lower()
    if response not in ['yes', 'y']:
        print("Cancelled by user.")
        sys.exit(0)

    print()
    print("Connecting to Bybit...")

    try:
        from pybit.unified_trading import HTTP

        # Create Bybit client
        session = HTTP(
            testnet=False,
            api_key=API_KEY,
            api_secret=API_SECRET,
        )

        print("✓ Connected to Bybit")
        print()

        # Get positions
        print("Fetching open positions...")
        response = session.get_positions(
            category="linear",
            settleCoin="USDT",
        )

        if response['retCode'] != 0:
            print(f"ERROR: Failed to fetch positions - {response['retMsg']}")
            sys.exit(1)

        positions = response['result']['list']

        if not positions:
            print("No open positions found.")
            return

        # Filter for BZ and CL with non-zero size
        relevant_positions = []
        for pos in positions:
            symbol = pos.get('symbol', '')
            size = float(pos.get('size', 0))

            if size > 0 and ('BZUSDT' in symbol or 'CLUSDT' in symbol):
                relevant_positions.append(pos)

        if not relevant_positions:
            print("No BZ or CL positions with non-zero size found.")
            return

        print(f"Found {len(relevant_positions)} position(s) to close:")
        print()

        # Display positions
        total_unrealized_pnl = 0.0
        for pos in relevant_positions:
            symbol = pos.get('symbol', 'UNKNOWN')
            size = float(pos.get('size', 0))
            side = pos.get('side', 'UNKNOWN')
            entry_price = float(pos.get('avgPrice', 0))
            unrealized_pnl = float(pos.get('unrealisedPnl', 0))
            leverage = pos.get('leverage', '1')

            total_unrealized_pnl += unrealized_pnl

            print(f"  • {symbol}: {side} {size} @ ${entry_price:.2f} ({leverage}x)")
            print(f"    Unrealized P&L: ${unrealized_pnl:.2f}")
            print()

        print(f"Total Unrealized P&L: ${total_unrealized_pnl:.2f}")
        print()

        # Final confirmation
        print("⚠️  FINAL CONFIRMATION")
        confirm = input("Close all these positions with MARKET orders? (yes/no): ").strip().lower()
        if confirm not in ['yes', 'y']:
            print("Cancelled by user.")
            return

        print()
        print("Closing positions...")
        print()

        # Close each position
        closed_count = 0
        failed_count = 0

        for pos in relevant_positions:
            symbol = pos.get('symbol', '')
            size = float(pos.get('size', 0))
            side = pos.get('side', '')

            if size == 0:
                continue

            # Determine close side (opposite of current position)
            close_side = "Sell" if side == "Buy" else "Buy"

            try:
                # Submit market order to close
                order_response = session.place_order(
                    category="linear",
                    symbol=symbol,
                    side=close_side,
                    orderType="Market",
                    qty=str(size),
                    reduceOnly=True,  # Important: only close, don't reverse
                )

                if order_response['retCode'] == 0:
                    order_id = order_response['result']['orderId']
                    print(f"  ✓ {symbol}: Closed {side} {size} (Order ID: {order_id})")
                    closed_count += 1
                else:
                    print(f"  ✗ {symbol}: Failed to close - {order_response['retMsg']}")
                    failed_count += 1

            except Exception as e:
                print(f"  ✗ {symbol}: Failed to close - {e}")
                failed_count += 1

        print()
        print("=" * 80)
        print(f"Position closure complete! Closed: {closed_count}, Failed: {failed_count}")
        print("=" * 80)
        print()

        if closed_count > 0:
            print("⏳ Waiting 3 seconds for orders to settle...")
            import time
            time.sleep(3)

            # Verify positions are closed
            print()
            print("Verifying positions are closed...")
            verify_response = session.get_positions(
                category="linear",
                settleCoin="USDT",
            )

            if verify_response['retCode'] == 0:
                remaining_positions = verify_response['result']['list']
                remaining_relevant = [
                    p for p in remaining_positions
                    if float(p.get('size', 0)) > 0 and ('BZUSDT' in p.get('symbol', '') or 'CLUSDT' in p.get('symbol', ''))
                ]

                if not remaining_relevant:
                    print("✓ All positions successfully closed!")
                else:
                    print(f"⚠️  Warning: {len(remaining_relevant)} position(s) still open:")
                    for p in remaining_relevant:
                        print(f"  • {p['symbol']}: {p['side']} {p['size']}")

        print()
        print("Please verify on Bybit UI that all positions are closed:")
        print("https://www.bybit.com/trade/usdt/BZUSDT")
        print()
        print("Then you can safely restart the strategy with the fixes.")

    except ImportError:
        print("ERROR: pybit library not found.")
        print()
        print("Installing pybit...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pybit"])
        print()
        print("Please run the script again.")
        sys.exit(1)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        print()
        print("=" * 80)
        print("Alternative: Close positions manually on Bybit UI")
        print("https://www.bybit.com/trade/usdt/BZUSDT")
        print("https://www.bybit.com/trade/usdt/CLUSDT")
        print("=" * 80)
        sys.exit(1)


if __name__ == "__main__":
    main()
