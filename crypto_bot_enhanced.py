import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import time
import json
import smtplib
from email.mime.text import MIMEText
from typing import Dict, Optional, List
import requests

class AlertManager:
    """Handle notifications via email, Discord, or console"""
    
    def __init__(self):
        self.email_enabled = False
        self.discord_enabled = False
        self.console_enabled = True
    
    def enable_email(self, sender_email: str, sender_password: str, recipient_email: str):
        """Enable email alerts"""
        self.email_enabled = True
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.recipient_email = recipient_email
    
    def enable_discord(self, webhook_url: str):
        """Enable Discord alerts"""
        self.discord_enabled = True
        self.webhook_url = webhook_url
    
    def send_alert(self, title: str, message: str, alert_type: str = "INFO"):
        """Send alert to all enabled channels"""
        
        # Console alert
        if self.console_enabled:
            emoji_map = {"BUY": "🟢", "SELL": "🔴", "WARNING": "⚠️", "INFO": "ℹ️"}
            emoji = emoji_map.get(alert_type, "")
            print(f"{emoji} [{alert_type}] {title}")
            print(f"   {message}\n")
        
        # Email alert
        if self.email_enabled:
            try:
                msg = MIMEText(message)
                msg['Subject'] = f"[{alert_type}] {title}"
                msg['From'] = self.sender_email
                msg['To'] = self.recipient_email
                
                server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
                server.quit()
            except Exception as e:
                print(f"Email alert failed: {e}")
        
        # Discord alert
        if self.discord_enabled:
            try:
                payload = {
                    "content": f"**[{alert_type}] {title}**\n{message}"
                }
                requests.post(self.webhook_url, json=payload)
            except Exception as e:
                print(f"Discord alert failed: {e}")

class CryptoTradingBot:
    def __init__(self, 
                 exchange_name: str = "binance",
                 api_key: str = None,
                 api_secret: str = None,
                 symbol: str = "BTC/USDT",
                 account_type: str = "paper",
                 starting_balance: float = 1000):
        """
        Initialize the trading bot
        
        Args:
            exchange_name: 'binance', 'kraken', 'coinbase', etc.
            api_key: Exchange API key (not needed for paper trading)
            api_secret: Exchange API secret (not needed for paper trading)
            symbol: Trading pair (e.g., 'BTC/USDT')
            account_type: 'paper' (simulated), 'demo' (exchange demo), 'live' (real money), 'prop' (prop firm)
            starting_balance: Balance for paper trading
        """
        self.exchange_name = exchange_name.lower()
        self.symbol = symbol
        self.account_type = account_type
        self.alerts = AlertManager()
        
        # Initialize exchange
        if account_type in ["demo", "live", "prop"]:
            try:
                exchange_class = getattr(ccxt, self.exchange_name)
                self.exchange = exchange_class({
                    'apiKey': api_key or '',
                    'secret': api_secret or '',
                    'enableRateLimit': True
                })
                
                if account_type == "demo":
                    self.exchange.sandbox = True  # Use testnet/demo
                
                self._initialize_balances()
            except Exception as e:
                print(f"✗ Exchange connection failed: {e}")
                raise
        else:
            # Paper trading - no real API needed
            self.exchange = None
            self.paper_balance = starting_balance
            self.starting_balance = starting_balance
            print(f"✓ Paper trading initialized")
            print(f"  Starting balance: ${self.paper_balance:.2f}")
        
        # Strategy parameters
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.position_size_percent = 0.95
        self.stop_loss_percent = 2
        self.take_profit_percent = 5
        
        # Risk management
        self.max_positions = 1
        self.max_loss_per_day = 0.05
        
        # State tracking
        self.current_position = None
        self.entry_price = None
        self.trade_history = []
        self.daily_pnl = 0
        
        self.alerts.send_alert(
            "Bot Started",
            f"Account type: {account_type.upper()}\nSymbol: {symbol}",
            "INFO"
        )
    
    def _initialize_balances(self):
        """Get account balance from exchange"""
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {}).get('free', 0)
            self.starting_balance = usdt_balance
            print(f"✓ Connected to {self.exchange_name} ({self.account_type.upper()})")
            print(f"  Starting balance: ${usdt_balance:.2f}")
        except Exception as e:
            print(f"✗ Balance fetch error: {e}")
            raise
    
    def calculate_rsi(self, prices: list, period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index)"""
        if len(prices) < period + 1:
            return None
        
        deltas = np.diff(prices[-period-1:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100 if avg_gain > 0 else 0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def fetch_candles(self, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data"""
        try:
            if self.account_type == "paper":
                # For paper trading, use real market data from Binance
                temp_exchange = ccxt.binance()
                candles = temp_exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)
            else:
                candles = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)
            
            df = pd.DataFrame(
                candles,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"✗ Error fetching candles: {e}")
            return None
    
    def get_signal(self, df: pd.DataFrame) -> str:
        """Generate trading signal based on RSI"""
        if df is None or len(df) < self.rsi_period + 1:
            return 'HOLD'
        
        rsi = self.calculate_rsi(df['close'].values, self.rsi_period)
        
        if rsi is None:
            return 'HOLD'
        
        # Buy signal
        if rsi < self.rsi_oversold and self.current_position is None:
            return 'BUY'
        
        # Sell signals
        if self.current_position is not None:
            current_price = df['close'].iloc[-1]
            pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
            
            if rsi > self.rsi_overbought:
                return 'SELL'
            elif pnl_percent < -self.stop_loss_percent:
                return 'SELL'
            elif pnl_percent > self.take_profit_percent:
                return 'SELL'
        
        return 'HOLD'
    
    def execute_buy(self, current_price: float) -> bool:
        """Execute a buy order"""
        if self.current_position is not None:
            return False
        
        if self.daily_pnl < -self.max_loss_per_day * self.starting_balance:
            self.alerts.send_alert("Daily Loss Limit", f"Hit ${-self.daily_pnl:.2f} loss, stopping", "WARNING")
            return False
        
        try:
            if self.account_type == "paper":
                available_balance = self.paper_balance
            else:
                available_balance = self.exchange.fetch_balance()['USDT']['free']
            
            position_value = available_balance * self.position_size_percent
            
            if position_value < 10:
                self.alerts.send_alert("Insufficient Balance", f"${position_value:.2f} < $10 minimum", "WARNING")
                return False
            
            amount = position_value / current_price
            
            if self.account_type == "paper":
                self.current_position = {'amount': amount, 'entry_price': current_price}
                self.entry_price = current_price
                self.paper_balance -= position_value
                self.alerts.send_alert("BUY", f"Bought {amount:.6f} {self.symbol.split('/')[0]} @ ${current_price:.2f}", "BUY")
            else:
                order = self.exchange.create_market_buy_order(self.symbol, amount)
                self.current_position = order
                self.entry_price = current_price
                self.alerts.send_alert("BUY", f"Bought {amount:.6f} @ ${current_price:.2f} (ORDER ID: {order['id']})", "BUY")
            
            return True
        except Exception as e:
            self.alerts.send_alert("Buy Error", str(e), "WARNING")
            return False
    
    def execute_sell(self, current_price: float) -> bool:
        """Execute a sell order"""
        if self.current_position is None:
            return False
        
        try:
            if self.account_type == "paper":
                amount = self.current_position['amount']
                position_value = amount * current_price
                pnl = position_value - (amount * self.entry_price)
                
                self.paper_balance += position_value
                self.daily_pnl += pnl
                
                pnl_percent = (pnl / (amount * self.entry_price)) * 100
                self.alerts.send_alert("SELL", f"Sold {amount:.6f} @ ${current_price:.2f}\nPnL: ${pnl:.2f} ({pnl_percent:.2f}%)", "SELL")
            else:
                amount = self.current_position.get('amount', 0)
                order = self.exchange.create_market_sell_order(self.symbol, amount)
                pnl = (current_price - self.entry_price) * amount
                self.daily_pnl += pnl
                self.alerts.send_alert("SELL", f"Sold {amount:.6f} @ ${current_price:.2f}\nOrder ID: {order['id']}", "SELL")
            
            self.trade_history.append({
                'timestamp': datetime.now(),
                'action': 'SELL',
                'price': current_price,
                'pnl': pnl
            })
            
            self.current_position = None
            self.entry_price = None
            return True
        except Exception as e:
            self.alerts.send_alert("Sell Error", str(e), "WARNING")
            return False
    
    def run_loop(self, interval_seconds: int = 60, max_iterations: Optional[int] = None):
        """Main trading loop"""
        iteration = 0
        print(f"\n🤖 Starting trading loop ({self.symbol})...")
        print(f"   Account type: {self.account_type.upper()}")
        print(f"   Interval: {interval_seconds}s\n")
        
        try:
            while True:
                iteration += 1
                
                if max_iterations and iteration > max_iterations:
                    print("✓ Reached max iterations")
                    break
                
                try:
                    df = self.fetch_candles('1h', limit=100)
                    if df is None:
                        time.sleep(interval_seconds)
                        continue
                    
                    current_price = df['close'].iloc[-1]
                    signal = self.get_signal(df)
                    rsi = self.calculate_rsi(df['close'].values, self.rsi_period)
                    
                    status = f"RSI: {rsi:.1f} | Price: ${current_price:.2f} | Signal: {signal}"
                    
                    if self.current_position:
                        pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                        status += f" | Open PnL: {pnl_pct:.2f}%"
                    
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {status}")
                    
                    if signal == 'BUY':
                        self.execute_buy(current_price)
                    elif signal == 'SELL':
                        self.execute_sell(current_price)
                    
                    time.sleep(interval_seconds)
                
                except KeyboardInterrupt:
                    print("\n⏹ Stopping bot (Ctrl+C)")
                    self.alerts.send_alert("Bot Stopped", "Trading bot stopped by user", "INFO")
                    break
                except Exception as e:
                    print(f"✗ Loop error: {e}")
                    time.sleep(interval_seconds)
        
        finally:
            self.print_summary()
    
    def print_summary(self):
        """Print trading summary"""
        if self.account_type == "paper":
            current_balance = self.paper_balance
        else:
            try:
                current_balance = self.exchange.fetch_balance()['USDT']['free']
            except:
                current_balance = self.starting_balance
        
        total_pnl = current_balance - self.starting_balance
        pnl_percent = (total_pnl / self.starting_balance) * 100
        
        print("\n" + "="*50)
        print("📊 TRADING SUMMARY")
        print("="*50)
        print(f"Account type: {self.account_type.upper()}")
        print(f"Starting balance: ${self.starting_balance:.2f}")
        print(f"Current balance: ${current_balance:.2f}")
        print(f"Total PnL: ${total_pnl:.2f} ({pnl_percent:.2f}%)")
        print(f"Trades completed: {len(self.trade_history)}")
        if self.current_position:
            print(f"Status: POSITION OPEN")
        print("="*50 + "\n")

# Example usage
if __name__ == "__main__":
    # ============================================
    # OPTION 1: PAPER TRADING (Simulated - Free)
    # ============================================
    bot = CryptoTradingBot(
        account_type="paper",
        symbol="BTC/USDT",
        starting_balance=1000  # Fake $1000
    )
    
    # ============================================
    # OPTION 2: DEMO ACCOUNT (Exchange Testnet)
    # ============================================
    # bot = CryptoTradingBot(
    #     exchange_name="binance",
    #     api_key="your_demo_key",
    #     api_secret="your_demo_secret",
    #     account_type="demo",
    #     symbol="BTC/USDT"
    # )
    
    # ============================================
    # OPTION 3: REAL ACCOUNT (Live Trading)
    # ============================================
    # bot = CryptoTradingBot(
    #     exchange_name="binance",
    #     api_key="your_real_key",
    #     api_secret="your_real_secret",
    #     account_type="live",
    #     symbol="BTC/USDT"
    # )
    
    # ============================================
    # ENABLE NOTIFICATIONS (Optional)
    # ============================================
    
    # Email alerts (Gmail example)
    # bot.alerts.enable_email(
    #     sender_email="your_email@gmail.com",
    #     sender_password="your_app_password",  # Use App Password, not regular password
    #     recipient_email="your_email@gmail.com"
    # )
    
    # Discord alerts
    # bot.alerts.enable_discord(
    #     webhook_url="https://discordapp.com/api/webhooks/YOUR_WEBHOOK_URL"
    # )
    
    # Run the bot
    bot.run_loop(interval_seconds=60, max_iterations=None)
