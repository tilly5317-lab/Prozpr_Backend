export type ChatRequest = {
  message: string;
  session_id?: string;
};

export type ChatResponse = {
  message: string;
  session_id: string;
  is_complete: boolean;
  next_field?: string | null;
  fields_collected: number;
  total_fields: number;
  progress_percentage: number;

  // NEW
  interaction_type: 'profile_update' | 'market_commentary';
};


export type SaveClientResponse = {
  client_id: number;
  message: string;
};

export type ClientSummary = {
  id: number;
  client_name: string;
  occupation: string;
  primary_objective: string;
  overall_risk: string;
  currency: string;
  created_at: string;
};

export type ClientListResponse = {
  clients: ClientSummary[];
  total: number;
};

export type Goal = {
  description: string;
  target_year: number;
  goal_type: string;
};

export type ClientDetailResponse = {
  client_id: number;
  client_name: string;
  created_at: string;
  snapshot: {
    goals: Goal[];
    profile_summary?: string | null;
    risk_return_assessment?: string | null;
    goals_alignment_assessment?: string | null;

    return_objective: {
      primary_objectives: string;
      description?: string | null;
      required_rate_of_return?: number | null;
      income_requirement?: number | null;
      currency?: string | null;
    };
    };
    risk_tolerance: {
      overall_risk_tolerance?: string | null;
      ability_to_take_risk?: string | null;
      willingness_to_take_risk?: string | null;
      ability_drivers?: string | null;
      willingness_drivers?: string | null;
    };
    strategic_asset_allocation?: Record<string, number | null> | null;
  };
  balance_sheet: {
    net_worth: number;
    total_assets: number;
    total_liabilities: number;
  };
  cash_flow_projection: Array<{
    year: number;
    income_post_tax: number;
    regular_expenses: number;
    mortgage_emi_paid: number;
    one_off_inflows: number;
    one_off_outflows: number;
    goal_outflow: number;
    opening_net_worth: number;
    roi_earned: number;
    closing_net_worth: number;
    mortgage_balance: number;
  }>;
};

export type ConversationMessage = {
  id: string;
  role: 'assistant' | 'user';
  content: string;
};
