# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Shopify-Notion sync application built as a Vercel serverless function using Python 3.9. The project is currently in proof-of-concept stage with a basic serverless endpoint that returns static responses.

## Architecture

- **Platform**: Vercel serverless functions
- **Runtime**: Python 3.9
- **Main endpoint**: `/api/sync` (handles both GET and POST requests)
- **Dependencies**: `requests` and `notion-client` Python packages

The application uses Python's built-in `http.server.BaseHTTPRequestHandler` as the base class for the serverless function handler in `api/sync.py`.

## Core Components

- `api/sync.py`: Main serverless function handler with GET/POST/OPTIONS methods
- `vercel.json`: Vercel configuration specifying Python 3.9 runtime
- `requirements.txt`: Python dependencies (requests, notion-client)

## Development Commands

This project uses Vercel for deployment and doesn't have traditional build/test scripts. Development workflow:

- **Local testing**: Use Vercel CLI (`vercel dev`) to run locally
- **Deployment**: Automatic deployment on push to main branch
- **Dependencies**: `pip install -r requirements.txt`

## Environment Variables (Not Yet Implemented)

The following environment variables will be needed for full functionality:
- `SHOPIFY_STORE_URL`
- `SHOPIFY_ACCESS_TOKEN`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

## Current Implementation Status

- ✅ Basic Vercel serverless function structure
- ✅ CORS headers for cross-origin requests
- ✅ JSON request/response handling
- 🔄 Shopify GraphQL integration (planned)
- 🔄 Notion database updates (planned)

## Key Implementation Notes

- The handler supports GET (testing), POST (main sync), and OPTIONS (CORS preflight)
- Error handling returns 500 status with error details
- All responses include CORS headers for browser compatibility
- Static responses are currently returned for testing purposes