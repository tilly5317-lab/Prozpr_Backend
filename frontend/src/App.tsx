import { useMemo, useRef, useState } from 'react';
import { getClientDetail, listClients, saveClientFromSession, sendChat } from './api';
import type { ClientDetailResponse, ClientSummary, ConversationMessage } from './types';

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number') {
    return 'Not provided';
  }
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(value);
}

function App() {
  const [activeView, setActiveView] = useState<'client' | 'advisor'>('client');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [progressLabel, setProgressLabel] = useState('Not started');
  const [error, setError] = useState('');
  const [clientId, setClientId] = useState<number | null>(null);
  const [ips, setIps] = useState<ClientDetailResponse | null>(null);
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [selectedAdvisorClientId, setSelectedAdvisorClientId] = useState<number | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const chatRef = useRef<HTMLDivElement | null>(null);

  const canSend = input.trim().length > 0 && !isLoading && sessionId !== null;

  const lastAssistantMessage = useMemo(
    () => [...messages].reverse().find((m) => m.role === 'assistant')?.content ?? '',
    [messages]
  );

  async function startConversation() {
    setError('');
    setIps(null);
    setClientId(null);
    setMessages([]);
    setInput('');
    setEditingMessageId(null);
    setIsComplete(false);
    setProgressLabel('Starting...');
    setIsLoading(true);

    try {
      const response = await sendChat({ message: 'Hi, I want to start' });
      setSessionId(response.session_id);
      setMessages([
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.message
        }
      ]);
      setIsComplete(response.is_complete);
      setProgressLabel(
        `${response.fields_collected}/${response.total_fields} fields (${response.progress_percentage.toFixed(1)}%)`
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  }

  async function submitAnswer() {
    if (!canSend) {
      return;
    }

    const answerToSend = input.trim();
    setError('');
    setIsLoading(true);

    try {
      const nextMessages: ConversationMessage[] = [
        ...messages,
        {
          id: crypto.randomUUID(),
          role: 'user',
          content:
            editingMessageId !== null
              ? `Edited response: ${answerToSend}`
              : answerToSend
        }
      ];
      setMessages(nextMessages);
      setInput('');
      setEditingMessageId(null);


      const response = await sendChat({
        message: answerToSend,
        session_id: sessionId ?? undefined,
      });

      setSessionId(response.session_id);
      setMessages([
        ...nextMessages,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.message,
        },
      ]);
      setIsComplete(response.is_complete);
      setProgressLabel(
        `${response.fields_collected}/${response.total_fields} fields (${response.progress_percentage.toFixed(1)}%)`
      );

      // Optionally, show a small tag indicating how the agent treated the message:
      const interactionNote =
        response.interaction_type === 'profile_update'
          ? 'I have used this information to update your profile and IPS assumptions.'
          : 'This was treated as a market discussion; your IPS profile remains unchanged.';
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: interactionNote,
        },
      ]);

      
      requestAnimationFrame(() => {
        if (chatRef.current) {
          chatRef.current.scrollTop = chatRef.current.scrollHeight;
        }
      });
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  }

  async function generateIps() {
    if (!sessionId) {
      return;
    }

    setError('');
    setIsLoading(true);

    try {
      const saveResult = await saveClientFromSession(sessionId);
      setClientId(saveResult.client_id);

      const detail = await getClientDetail(saveResult.client_id);
      setIps(detail);
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  }

  async function loadClientsForAdvisor() {
    setError('');
    setIsLoading(true);
    try {
      const response = await listClients();
      setClients(response.clients);
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  }

  async function loadAdvisorClientIps(targetClientId: number) {
    setError('');
    setIsLoading(true);
    try {
      const detail = await getClientDetail(targetClientId);
      setSelectedAdvisorClientId(targetClientId);
      setIps(detail);
      setActiveView('advisor');
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  }

  function startEditAnswer(messageId: string, content: string) {
    setEditingMessageId(messageId);
    setInput(content.replace('Edited response: ', ''));
  }

  return (
    <div className="app">
      <h1>Ailax Financial Planner</h1>
      <div>Use the advisor button to begin question-by-question client data capture.</div>

      <div className="panel">
        <div className="viewTabs">
          <button
            className={activeView === 'client' ? 'primary' : 'secondary'}
            onClick={() => setActiveView('client')}
            disabled={isLoading}
          >
            Client Conversation
          </button>
          <button
            className={activeView === 'advisor' ? 'primary' : 'secondary'}
            onClick={async () => {
              setActiveView('advisor');
              await loadClientsForAdvisor();
            }}
            disabled={isLoading}
          >
            Advisor Dashboard
          </button>
        </div>
      </div>

      {activeView === 'client' ? (
        <>
          <div className="panel">
            <button className="primary" onClick={startConversation} disabled={isLoading}>
              Financial Advisor Agent
            </button>
            <div className="status">Status: {progressLabel}</div>
          </div>

          <div className="panel">
            <h2>Conversation</h2>
            <div className="chatWindow" ref={chatRef}>
              {messages.map((message) => (
                <div key={message.id} className={`msg ${message.role}`}>
                  <strong>{message.role === 'assistant' ? 'Agent' : 'Client'}:</strong> {message.content}
                  {message.role === 'user' ? (
                    <div className="msgActions">
                      <button
                        className="secondary"
                        onClick={() => startEditAnswer(message.id, message.content)}
                        disabled={isLoading}
                      >
                        Edit & Resubmit
                      </button>
                    </div>
                  ) : null}
                </div>
              ))}
              {messages.length === 0 ? <div>No conversation yet.</div> : null}
            </div>

            <div className="chatInputRow">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={
                  editingMessageId
                    ? 'Edit your previous answer and click Submit Answer'
                    : 'Type your answer here'
                }
                disabled={isLoading || sessionId === null}
              />
              <button className="primary" onClick={submitAnswer} disabled={!canSend}>
                Submit Answer
              </button>
            </div>

            <div className="status">
              Latest agent prompt: {lastAssistantMessage || 'Not available yet'}
            </div>

            {isComplete ? (
              <div style={{ marginTop: 10 }}>
                <button className="primary" onClick={generateIps} disabled={isLoading}>
                  Save Client Data & Generate Investment Policy Statement
                </button>
              </div>
            ) : null}

            {error ? <div className="error">{error}</div> : null}
          </div>
        </>
      ) : (
        <div className="panel">
          <h2>Advisor Dashboard</h2>
          <div className="chatInputRow">
            <button className="primary" onClick={loadClientsForAdvisor} disabled={isLoading}>
              Refresh Saved Clients
            </button>
          </div>
          <div className="status">Saved clients: {clients.length}</div>
          <div className="advisorList">
            {clients.map((record) => (
              <div className="advisorRow" key={record.id}>
                <div>
                  <strong>{record.client_name}</strong> ({record.occupation || 'N/A'}) • Objective:{' '}
                  {record.primary_objective || 'N/A'} • Risk: {record.overall_risk || 'N/A'}
                </div>
                <button
                  className="secondary"
                  onClick={() => loadAdvisorClientIps(record.id)}
                  disabled={isLoading}
                >
                  View IPS
                </button>
              </div>
            ))}
            {clients.length === 0 ? <div>No saved clients yet.</div> : null}
          </div>
          {error ? <div className="error">{error}</div> : null}
          {selectedAdvisorClientId ? (
            <div className="status">Viewing client ID: {selectedAdvisorClientId}</div>
          ) : null}
        </div>
      )}

      {ips ? (
        <div className="panel">
          <h2>Investment Policy Statement</h2>
          <div className="status">Saved client ID: {clientId}</div>

          <div className="ipsGrid">
            <div className="ipsCard">
              <h3>Client Goals</h3>
              <ul>
                {ips.snapshot.goals.length > 0 ? (
                  ips.snapshot.goals.map((goal, idx) => (
                    <li key={`${goal.description}-${idx}`}>
                      {goal.description} ({goal.goal_type}) by {goal.target_year}
                    </li>
                  ))
                ) : (
                  <li>Not provided</li>
                )}
              </ul>
            </div>

            <div className="ipsCard">
              <h3>Client Profile & Risk / Return Assessment</h3>
              <ul>
                <li>Profile: {ips.snapshot.profile_summary ?? 'Not provided'}</li>
                <li>Risk & Return: {ips.snapshot.risk_return_assessment ?? 'Not provided'}</li>
                <li>Goals Alignment: {ips.snapshot.goals_alignment_assessment ?? 'Not provided'}</li>
              </ul>
            </div>

            <div className="ipsCard">
              <h3>Return Objective</h3>
              <ul>
                <li>Primary: {ips.snapshot.return_objective.primary_objectives || 'Not provided'}</li>
                <li>Description: {ips.snapshot.return_objective.description || 'Not provided'}</li>
                <li>
                  Required Rate of Return:{' '}
                  {formatNumber(ips.snapshot.return_objective.required_rate_of_return)}
                </li>
                <li>
                  Income Requirement: {formatNumber(ips.snapshot.return_objective.income_requirement)}
                </li>
              </ul>
            </div>

            <div className="ipsCard">
              <h3>Risk Tolerance</h3>
              <ul>
                <li>
                  Overall: {ips.snapshot.risk_tolerance.overall_risk_tolerance || 'Not provided'}
                </li>
                <li>
                  Ability: {ips.snapshot.risk_tolerance.ability_to_take_risk || 'Not provided'}
                </li>
                <li>
                  Willingness:{' '}
                  {ips.snapshot.risk_tolerance.willingness_to_take_risk || 'Not provided'}
                </li>
                <li>Ability Drivers: {ips.snapshot.risk_tolerance.ability_drivers || 'Not provided'}</li>
                <li>
                  Willingness Drivers:{' '}
                  {ips.snapshot.risk_tolerance.willingness_drivers || 'Not provided'}
                </li>
              </ul>
            </div>

            <div className="ipsCard">
              <h3>Cash Flow Planning</h3>
              <ul>
                {ips.cash_flow_projection.slice(0, 5).map((row) => (
                  <li key={row.year}>
                    {row.year}: Closing Net Worth {formatNumber(row.closing_net_worth)}; 
                    Goals: {formatNumber(row.goal_outflow)}; 
                    Mortgage Balance: {formatNumber(row.mortgage_balance)}
                  </li>
                ))}
              </ul>
            </div>

            <div className="ipsCard">
              <h3>Strategic Asset Allocation</h3>
              {ips.snapshot.strategic_asset_allocation ? (
                <ul>
                  {Object.entries(ips.snapshot.strategic_asset_allocation).map(([key, value]) => (
                    <li key={key}>
                      {key}: {String(value ?? 'Not provided')}
                    </li>
                  ))}
                </ul>
              ) : (
                <div>Not provided</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default App;
