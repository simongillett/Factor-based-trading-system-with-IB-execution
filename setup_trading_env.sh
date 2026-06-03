#!/bin/bash
# Setup script for production trading environment

echo "🚀 Setting up production trading environment..."

# Activate trading environment
source trading_env/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install core trading packages
echo "📦 Installing IBKR API packages..."
pip install ibapi>=9.81.1.post1
pip install ib-insync>=0.9.86

# Install data processing packages
echo "📊 Installing data processing packages..."
pip install pandas>=2.0.0
pip install numpy>=1.24.0
pip install polars>=0.20.0
pip install duckdb>=0.9.0

# Install machine learning packages
echo "🤖 Installing ML packages..."
pip install scikit-learn>=1.3.0

# Install scheduling and async
echo "⏰ Installing scheduling packages..."
pip install schedule>=1.2.0
pip install asyncio-mqtt>=0.13.0

# Install risk management and backtesting
echo "📈 Installing risk management packages..."
pip install pyfolio>=0.9.2
pip install empyrical>=0.5.5

# Install logging and monitoring
echo "📝 Installing logging packages..."
pip install structlog>=23.1.0
pip install prometheus-client>=0.17.0

# Install configuration management
echo "⚙️ Installing configuration packages..."
pip install pydantic>=2.0.0
pip install python-dotenv>=1.0.0

# Install utilities
echo "🔧 Installing utilities..."
pip install python-dateutil>=2.8.0
pip install pytz>=2023.3
pip install requests>=2.31.0

# Install development tools
echo "🛠️ Installing development tools..."
pip install pytest>=7.4.0
pip install black>=23.7.0
pip install flake8>=6.0.0

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p logs
mkdir -p results
mkdir -p backups

# Set permissions
chmod +x trading_execution_engine.py

echo "✅ Trading environment setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Update your IBKR account ID in trading_config.json"
echo "2. Ensure TWS/Gateway is running on port 7496"
echo "3. Test connection with: python trading_execution_engine.py"
echo "4. Set dry_run to false in config when ready for live trading"
echo ""
echo "⚠️  IMPORTANT: Start with paper trading (dry_run: true) to test the system!"
