#!/bin/bash
# Quick setup script for local development testing

set -e

echo "🚀 Setting up Octopoid v2.0 for local testing..."

# Use npx pnpm to avoid requiring global install
echo "📦 Installing dependencies..."
npx -y pnpm install

# Build packages
echo "🔨 Building packages..."
cd packages/shared && npx pnpm build && cd ../..
cd packages/server && npx pnpm build && cd ../..
cd packages/client && npx pnpm build && cd ../..

# Link client for global use
echo "🔗 Linking client..."
cd packages/client
npm link
cd ../..

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Start the server:"
echo "   cd packages/server && wrangler dev"
echo ""
echo "2. In another terminal, initialize the client:"
echo "   octopoid init --server http://localhost:8787 --cluster dev"
echo ""
echo "3. Start the orchestrator:"
echo "   octopoid start --debug"
