# Wealth Planning Agent Frontend

This folder will contain the web frontend for your Wealth Planning Agent.

The idea is:

- **Clients** use a chat + voice interface to answer questions about their financial situation.
- The frontend calls the backend API (`/chat`, `/clients`, `/clients/{id}`) exposed by the FastAPI app in `backend/app/main.py`.
- **Advisor** uses either this frontend (e.g. `/advisor` route) or the existing Streamlit app as the dashboard.

## Getting started

1. Create a React + TypeScript app with Vite:

   ```bash
   cd frontend
   npm create vite@latest wealth-frontend -- --template react-ts
   cd wealth-frontend
   npm install
   ```

2. Install additional dependencies:

   ```bash
   npm install axios react-router-dom @types/react-router-dom
   npm install -D tailwindcss postcss autoprefixer
   npx tailwindcss init -p
   ```

3. Configure Tailwind CSS (optional but recommended for styling).

4. Create the folder structure:

   ```
   frontend/wealth-frontend/
   ├── src/
   │   ├── components/
   │   │   ├── Chat.tsx           # Chat interface component
   │   │   ├── VoiceInput.tsx     # Voice input component
   │   │   ├── ProgressBar.tsx    # Progress indicator
   │   │   └── ClientList.tsx     # Client list for advisor
   │   ├── pages/
   │   │   ├── ClientPage.tsx     # Client chat interface
   │   │   ├── AdvisorPage.tsx    # Advisor dashboard
   │   │   └── ClientDetailPage.tsx # Client details + analysis
   │   ├── services/
   │   │   └── api.ts             # API client for backend
   │   ├── types/
   │   │   └── index.ts           # TypeScript types
   │   ├── App.tsx
   │   └── main.tsx
   ```

5. Run the development server:

   ```bash
   npm run dev
   ```

## API Integration

The frontend will communicate with the FastAPI backend running at `http://localhost:8000`.

Key endpoints:
- `POST /chat` - Send user input and get next question
- `POST /clients` - Save completed client profile
- `GET /clients` - List all clients
- `GET /clients/{id}` - Get client details with financial analysis
- `GET /health` - Check backend health

## Features to Implement

### Client Interface
- [ ] Conversational chat UI
- [ ] Voice input/output (Web Speech API)
- [ ] Progress indicator (X/38 fields completed)
- [ ] Field validation and error handling
- [ ] Save/resume capability

### Advisor Dashboard
- [ ] Client list with search/filter
- [ ] Client detail view with:
  - Complete profile
  - Cash flow projections (chart)
  - Balance sheet (table)
  - Net worth summary
- [ ] Export to PDF (Investment Policy Statement)

## Environment Variables

Create a `.env` file:

```
VITE_API_URL=http://localhost:8000
```

## Deployment

- Build for production: `npm run build`
- Preview: `npm run preview`
- Deploy to Vercel/Netlify/etc.