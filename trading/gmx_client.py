"""
GMX v2 Client — Arbitrum One
Handles wallet connection, balance reading, position management,
and order execution on GMX decentralized perpetuals exchange.
"""

import os
import json
import time
import requests
from web3 import Web3
from eth_account import Account
from eth_account.signers.local import LocalAccount
from dotenv import load_dotenv

load_dotenv(os.path.expanduser('~/.thor/config/.env'))

# ── Arbitrum token addresses ─────────────────────────────────────────────────
USDC_ADDRESS = Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831')
WETH_ADDRESS = Web3.to_checksum_address('0x82aF49447D8a07e3bd95BD0d56f35241523fBab1')

# ── GMX v2 core contracts (Arbitrum One) ─────────────────────────────────────
GMX_EXCHANGE_ROUTER = Web3.to_checksum_address('0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8')
GMX_ROUTER          = Web3.to_checksum_address('0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6')
GMX_ORDER_VAULT     = Web3.to_checksum_address('0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5')
GMX_DATASTORE       = Web3.to_checksum_address('0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8')
GMX_READER          = Web3.to_checksum_address('0x38d91ED96283d62182Fc6d990C24097A918a4d9B')
UI_FEE_RECEIVER     = Web3.to_checksum_address('0x0000000000000000000000000000000000000000')

# ── GMX v2 market addresses on Arbitrum ─────────────────────────────────────
# market_token → {index_token, long_token, short_token}
GMX_MARKETS = {
    'BTC':  {
        'market':  '0x47c031236e19d024b42f8AE6780E44A573170703',
        'index':   '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',  # WBTC
        'long':    '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',
        'short':   USDC_ADDRESS,
    },
    'ETH':  {
        'market':  '0x70d95587d40A2caf56bd97485aB3Eec10Bee6336',
        'index':   WETH_ADDRESS,
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'SOL':  {
        'market':  '0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9',
        'index':   '0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07',  # SOL
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'ARB':  {
        'market':  '0xC25cEf6061Cf5dE5eb761b50E4743c1F5D7E5407',
        'index':   '0x912CE59144191C1204E64559FE8253a0e49E6548',  # ARB
        'long':    '0x912CE59144191C1204E64559FE8253a0e49E6548',
        'short':   USDC_ADDRESS,
    },
    'LINK': {
        'market':  '0x7f1fa204bb700853D36994DA19F830b6Ad18d9D5',
        'index':   '0xf97f4df75117a78c1A5a0DBb814Af92458539FB4',  # LINK
        'long':    '0xf97f4df75117a78c1A5a0DBb814Af92458539FB4',
        'short':   USDC_ADDRESS,
    },
    'DOGE': {
        'market':  '0x6853EA96FF216fAb11D2d930CE3C508556A4bdc4',
        'index':   '0xC4da4c24fd591125c3F47b340b6f4f76111883d8',  # DOGE
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'LTC':  {
        'market':  '0xD9535bB5f58A1a75032416F2dFe7880C30575a41',
        'index':   '0xB46A094Bc4B0adBD801E14b9DB95e05E28962764',  # LTC
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'XRP':  {
        'market':  '0x0CCB4fAa6f1F1B0f6395aA9afADaA227e67F0267',
        'index':   '0xc14e065b0067dE91534e032868f5Ac6ecf2c6868',  # XRP
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'BNB':  {
        'market':  '0x2d340912Aa47e33c90Efb078e69e70EFe2B34b9B',
        'index':   '0xa9004A5421372E1D83fB1f85b0fc986c912f91f3',  # BNB
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
    'AVAX': {
        'market':  '0x7BbBf946883a5701350007320F525c5379B8178A',
        'index':   '0x565609fAF65B92F7be02468acF86f8979423e514',  # AVAX
        'long':    WETH_ADDRESS,
        'short':   USDC_ADDRESS,
    },
}

# ── Minimal ABIs ─────────────────────────────────────────────────────────────
ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
]

EXCHANGE_ROUTER_ABI = [
    {
        "inputs": [{"name": "data", "type": "bytes[]"}],
        "name": "multicall",
        "outputs": [{"name": "results", "type": "bytes[]"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "sendTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "receiver", "type": "address"}],
        "name": "sendWnt",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [{
            "components": [
                {"components": [
                    {"name": "receiver",              "type": "address"},
                    {"name": "cancellationReceiver",  "type": "address"},
                    {"name": "callbackContract",      "type": "address"},
                    {"name": "uiFeeReceiver",         "type": "address"},
                    {"name": "market",                "type": "address"},
                    {"name": "initialCollateralToken","type": "address"},
                    {"name": "swapPath",              "type": "address[]"},
                ], "name": "addresses", "type": "tuple"},
                {"components": [
                    {"name": "sizeDeltaUsd",                "type": "uint256"},
                    {"name": "initialCollateralDeltaAmount","type": "uint256"},
                    {"name": "triggerPrice",               "type": "uint256"},
                    {"name": "acceptablePrice",            "type": "uint256"},
                    {"name": "executionFee",               "type": "uint256"},
                    {"name": "callbackGasLimit",           "type": "uint256"},
                    {"name": "minOutputAmount",            "type": "uint256"},
                    {"name": "validFromTime",              "type": "uint256"},
                ], "name": "numbers", "type": "tuple"},
                {"name": "orderType",                  "type": "uint8"},
                {"name": "decreasePositionSwapType",   "type": "uint8"},
                {"name": "isLong",                     "type": "bool"},
                {"name": "shouldUnwrapNativeToken",    "type": "bool"},
                {"name": "autoCancel",                 "type": "bool"},
                {"name": "referralCode",               "type": "bytes32"},
            ],
            "name": "params", "type": "tuple"
        }],
        "name": "createOrder",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "payable",
        "type": "function"
    },
]

# GMX order types
ORDER_TYPE_MARKET_INCREASE = 2   # market open (long or short)
ORDER_TYPE_MARKET_DECREASE = 4   # market close


class GMXClient:
    """
    Connects THOR to GMX v2 on Arbitrum.
    Handles wallet balance, open positions, and order execution.
    """

    EXECUTION_FEE_ETH = 0.003      # ETH sent to keeper for execution (~$7-10)
    SLIPPAGE_BPS      = 50         # 0.5% acceptable price slippage
    USDC_DECIMALS     = 6
    PRICE_DECIMALS    = 30         # GMX uses 1e30 price precision

    def __init__(self):
        rpc_url          = os.getenv('ARBITRUM_RPC_URL', '')
        self.wallet_addr = os.getenv('TRADING_WALLET_ADDRESS', '')
        self._private_key= os.getenv('TRADING_WALLET_PRIVATE_KEY', '')
        self.max_leverage= float(os.getenv('GMX_MAX_LEVERAGE', '5'))
        self.max_pos_pct = float(os.getenv('GMX_MAX_POSITION_PCT', '0.20'))

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 20}))
        self._usdc   = self.w3.eth.contract(address=USDC_ADDRESS,          abi=ERC20_ABI)
        self._router = self.w3.eth.contract(address=GMX_EXCHANGE_ROUTER,   abi=EXCHANGE_ROUTER_ABI)

    # ── Connection ───────────────────────────────────────────────────────────

    def is_connected(self):
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def get_chain_id(self):
        return self.w3.eth.chain_id   # should be 42161 (Arbitrum)

    # ── Balances ─────────────────────────────────────────────────────────────

    def get_eth_balance(self):
        """Returns ETH balance in ether (float)."""
        if not self.wallet_addr:
            return 0.0
        try:
            wei = self.w3.eth.get_balance(Web3.to_checksum_address(self.wallet_addr))
            return float(self.w3.from_wei(wei, 'ether'))
        except Exception as e:
            print(f"ETH balance error: {e}")
            return 0.0

    def get_usdc_balance(self):
        """Returns USDC balance in USD (float)."""
        if not self.wallet_addr:
            return 0.0
        try:
            raw = self._usdc.functions.balanceOf(
                Web3.to_checksum_address(self.wallet_addr)
            ).call()
            return raw / 10 ** self.USDC_DECIMALS
        except Exception as e:
            print(f"USDC balance error: {e}")
            return 0.0

    def get_wallet_summary(self):
        eth   = self.get_eth_balance()
        usdc  = self.get_usdc_balance()
        # Fetch ETH price to compute total USD value
        try:
            r = requests.get('https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT', timeout=5)
            eth_price = float(r.json()['price'])
        except Exception:
            eth_price = 0.0
        eth_usd   = round(eth * eth_price, 2)
        total_usd = round(usdc + eth_usd, 2)
        return {
            'connected':   self.is_connected(),
            'address':     self.wallet_addr,
            'eth':         round(eth, 6),
            'eth_usd':     eth_usd,
            'usdc':        round(usdc, 2),
            'total_usd':   total_usd,
            'eth_price':   round(eth_price, 2),
        }

    # ── Live positions (via GMX subgraph) ────────────────────────────────────

    def get_open_positions(self):
        """Fetch open GMX positions for the trading wallet via subgraph."""
        if not self.wallet_addr:
            return []
        query = '''
        {
          positions(where: {account: "%s", sizeInUsd_gt: 0}, first: 20) {
            id
            market
            collateralToken
            sizeInUsd
            sizeInTokens
            collateralAmount
            isLong
            averageEntryPrice
            entryFundingAmountPerSize
            realizedPnlAfterFees
          }
        }
        ''' % self.wallet_addr.lower()
        try:
            resp = requests.post(
                'https://subgraph.satsuma-prod.com/3b2ced13c8d9/gmx/synthetics-arbitrum-stats/api',
                json={'query': query},
                timeout=10
            )
            data = resp.json().get('data', {}).get('positions', [])
            positions = []
            for p in data:
                market_addr = Web3.to_checksum_address(p['market'])
                symbol = next((k for k, v in GMX_MARKETS.items()
                               if Web3.to_checksum_address(v['market']) == market_addr), 'UNKNOWN')
                size_usd     = int(p['sizeInUsd']) / 1e30
                entry_price  = int(p['averageEntryPrice']) / 1e30
                collateral   = int(p['collateralAmount']) / 10 ** self.USDC_DECIMALS
                pnl          = int(p.get('realizedPnlAfterFees', 0)) / 1e30
                positions.append({
                    'symbol':      symbol,
                    'is_long':     p['isLong'],
                    'direction':   'LONG' if p['isLong'] else 'SHORT',
                    'size_usd':    round(size_usd, 2),
                    'entry_price': round(entry_price, 2),
                    'collateral':  round(collateral, 2),
                    'leverage':    round(size_usd / collateral, 2) if collateral > 0 else 0,
                    'pnl':         round(pnl, 2),
                })
            return positions
        except Exception as e:
            print(f"Positions fetch error: {e}")
            return []

    # ── Order execution ──────────────────────────────────────────────────────

    def _get_current_price(self, symbol):
        """Get live price from Binance for slippage calculation."""
        binance_sym = symbol + 'USDT'
        r = requests.get(
            f'https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}',
            timeout=5
        )
        return float(r.json()['price'])

    def _approve_usdc(self, amount_usdc):
        """Approve ExchangeRouter to spend USDC."""
        account: LocalAccount = Account.from_key(self._private_key)
        amount_raw = int(amount_usdc * 10 ** self.USDC_DECIMALS)
        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self._usdc.functions.approve(
            GMX_EXCHANGE_ROUTER, amount_raw
        ).build_transaction({
            'from':     account.address,
            'nonce':    nonce,
            'gas':      100_000,
            'gasPrice': self.w3.eth.gas_price,
            'chainId':  42161,
        })
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1

    def open_position(self, symbol, collateral_usdc, leverage, is_long):
        """
        Open a leveraged long or short on GMX v2.

        symbol         — 'BTC', 'ETH', etc.
        collateral_usdc — USDC amount to use as margin
        leverage        — 1–5x (capped by GMX_MAX_LEVERAGE)
        is_long         — True for LONG, False for SHORT
        """
        if not self._private_key:
            return {'ok': False, 'msg': 'No private key configured'}
        if symbol not in GMX_MARKETS:
            return {'ok': False, 'msg': f'Market {symbol} not available'}

        leverage = min(leverage, self.max_leverage)
        max_collateral = self.get_usdc_balance() * self.max_pos_pct
        collateral_usdc = min(collateral_usdc, max_collateral)
        if collateral_usdc < 2:
            return {'ok': False, 'msg': 'Insufficient USDC balance'}

        try:
            market     = GMX_MARKETS[symbol]
            account: LocalAccount = Account.from_key(self._private_key)
            price      = self._get_current_price(symbol)
            size_usd   = collateral_usdc * leverage

            # Price precision: GMX uses 1e30
            price_30   = int(price * 10 ** self.PRICE_DECIMALS)
            slippage   = price_30 * self.SLIPPAGE_BPS // 10_000
            acceptable = price_30 - slippage if is_long else price_30 + slippage

            exec_fee_wei = self.w3.to_wei(self.EXECUTION_FEE_ETH, 'ether')
            size_delta   = int(size_usd * 10 ** self.PRICE_DECIMALS)
            coll_amount  = int(collateral_usdc * 10 ** self.USDC_DECIMALS)

            # Step 1: approve USDC
            if not self._approve_usdc(collateral_usdc + 1):
                return {'ok': False, 'msg': 'USDC approval failed'}

            # Step 2: build multicall — sendWnt + sendTokens + createOrder
            send_wnt_data = self._router.encodeABI(
                fn_name='sendWnt',
                args=[GMX_ORDER_VAULT]
            )
            send_tokens_data = self._router.encodeABI(
                fn_name='sendTokens',
                args=[USDC_ADDRESS, coll_amount]
            )
            order_params = (
                (   # addresses
                    account.address,                          # receiver
                    account.address,                          # cancellationReceiver
                    '0x0000000000000000000000000000000000000000',  # callbackContract
                    UI_FEE_RECEIVER,                          # uiFeeReceiver
                    Web3.to_checksum_address(market['market']),  # market
                    USDC_ADDRESS,                             # initialCollateralToken
                    [],                                       # swapPath
                ),
                (   # numbers
                    size_delta,    # sizeDeltaUsd
                    coll_amount,   # initialCollateralDeltaAmount
                    0,             # triggerPrice (0 = market order)
                    acceptable,    # acceptablePrice
                    exec_fee_wei,  # executionFee
                    0,             # callbackGasLimit
                    0,             # minOutputAmount
                    0,             # validFromTime
                ),
                ORDER_TYPE_MARKET_INCREASE,  # orderType
                0,                           # decreasePositionSwapType (NoSwap)
                is_long,                     # isLong
                False,                       # shouldUnwrapNativeToken
                False,                       # autoCancel
                b'\x00' * 32,               # referralCode
            )
            create_order_data = self._router.encodeABI(
                fn_name='createOrder',
                args=[order_params]
            )

            nonce = self.w3.eth.get_transaction_count(account.address)
            tx = self._router.functions.multicall(
                [send_wnt_data, send_tokens_data, create_order_data]
            ).build_transaction({
                'from':     account.address,
                'value':    exec_fee_wei,
                'nonce':    nonce,
                'gas':      2_000_000,
                'gasPrice': self.w3.eth.gas_price,
                'chainId':  42161,
            })
            signed  = account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            direction = 'LONG' if is_long else 'SHORT'
            if receipt.status == 1:
                return {
                    'ok':        True,
                    'msg':       f'{direction} {symbol} ${collateral_usdc:,.0f} @ {leverage}x — order submitted',
                    'tx_hash':   tx_hash.hex(),
                    'symbol':    symbol,
                    'direction': direction,
                    'collateral': collateral_usdc,
                    'leverage':  leverage,
                    'size_usd':  size_usd,
                    'price':     price,
                }
            else:
                return {'ok': False, 'msg': f'Transaction reverted: {tx_hash.hex()}'}

        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    def close_position(self, symbol, is_long):
        """Close an open position on GMX v2 (full close)."""
        if not self._private_key:
            return {'ok': False, 'msg': 'No private key configured'}
        if symbol not in GMX_MARKETS:
            return {'ok': False, 'msg': f'Market {symbol} not available'}

        try:
            market  = GMX_MARKETS[symbol]
            account: LocalAccount = Account.from_key(self._private_key)
            price   = self._get_current_price(symbol)

            price_30   = int(price * 10 ** self.PRICE_DECIMALS)
            slippage   = price_30 * self.SLIPPAGE_BPS // 10_000
            acceptable = price_30 + slippage if is_long else price_30 - slippage
            exec_fee_wei = self.w3.to_wei(self.EXECUTION_FEE_ETH, 'ether')

            # For a full close, use MAX_UINT256 for sizeDeltaUsd
            MAX_UINT256 = 2 ** 256 - 1

            send_wnt_data = self._router.encodeABI(
                fn_name='sendWnt',
                args=[GMX_ORDER_VAULT]
            )
            order_params = (
                (
                    account.address,
                    account.address,
                    '0x0000000000000000000000000000000000000000',
                    UI_FEE_RECEIVER,
                    Web3.to_checksum_address(market['market']),
                    USDC_ADDRESS,
                    [],
                ),
                (
                    MAX_UINT256,   # sizeDeltaUsd = close entire position
                    0,
                    0,
                    acceptable,
                    exec_fee_wei,
                    0,
                    0,
                    0,
                ),
                ORDER_TYPE_MARKET_DECREASE,
                0,
                is_long,
                False,
                False,
                b'\x00' * 32,
            )
            create_order_data = self._router.encodeABI(
                fn_name='createOrder',
                args=[order_params]
            )

            nonce = self.w3.eth.get_transaction_count(account.address)
            tx = self._router.functions.multicall(
                [send_wnt_data, create_order_data]
            ).build_transaction({
                'from':     account.address,
                'value':    exec_fee_wei,
                'nonce':    nonce,
                'gas':      2_000_000,
                'gasPrice': self.w3.eth.gas_price,
                'chainId':  42161,
            })
            signed  = account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                return {
                    'ok':      True,
                    'msg':     f'Closed {symbol} {"LONG" if is_long else "SHORT"} — order submitted',
                    'tx_hash': tx_hash.hex(),
                    'price':   price,
                }
            else:
                return {'ok': False, 'msg': f'Transaction reverted: {tx_hash.hex()}'}

        except Exception as e:
            return {'ok': False, 'msg': str(e)}
