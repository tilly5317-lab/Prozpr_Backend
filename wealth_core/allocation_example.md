# Allocation planner reference

Scheme lists, classification rules, and heuristics used by `allocation_reasoning` (loaded at import and appended to the LLM prompt).

Different schemes:
1. Equity Schemes:
1.A. Multi Cap Fund
1.B. Large Cap Fund
1.C. Large & Mid Cap Fund
1.D. Mid Cap Fund
1.E. Small cap Fund
1.F. Flexi Cap Fund
1.G. Dividend Yield Fund
1.H. Value 
1.I. Contra 
1.J. Focused 
1.K. Sectoral 
1.L. Thematic
1.M. ELSS Tax Saver Fund


2. Debt Schemes:
2.A. Overnight Fund 
2.B. Liquid Fund 
2.C. Ultra Short Fund (3–6 months)
2.D. Ultra Short to Short Term Fund (6–12 months)
2.E. Money market Fund 
2.F. Short Term Fund  (1–3 years)
2.G. Medium Term Fund  (3–4 years) 
2.H. Medium Term to Long Term Fund  (4-7 years)
2.I. Long Term Fund  (above 7 years)
2.J. Dynamic Term Fund
2.K. Corporate Bond Fund 
2.L. Credit Risk Fund
2.M. Banking and PSU Debt Fund
2.N. Gilt Fund 
2.O. 10-year Constant Maturity Gilt Fund 
2.P. Floating Interest Rates Fund 
2.Q. Sectoral Fund 


3. Hybrid Schemes:
3.A. Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%)
3.B. Balanced Hybrid Fund  (Equity 40-60%, Debt 40-60%)
3.C. Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%) 
3.D. Dynamic Asset Allocation Fund
3.E. Multi-Asset Allocation Fund
3.F. Arbitrage Fund
3.G. 

4. Lifecycle Funds:
4.A. Glide Path Strategy
4.B. Goal-Based

5. Other Schemes:
5.A. Index Funds & ETFs:
5.B. Fund of Funds


1. Classification rules of mutual fund schemes
1.1 Top‑level asset_class
Every mutual fund scheme belongs to exactly one of these three groups:
Equities
Debt
Others

1.2 asset-class and asset_subgroups
Use this data to link specific mutual fund types to their respective asset classes and subgroups. Use these precise terms.

- 1.A. Multi Cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 1.B. Large Cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: low_beta_equities
- 1.C. Large & Mid Cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 1.D. Mid Cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 1.E. Small cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: high_beta_equities
- 1.F. Flexi Cap Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 1.G. Dividend Yield Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: dividend_equities
- 1.H. Value | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: value_equities
- 1.I. Contra | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: value_equities
- 1.J. Focused | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: high_beta_equities
- 1.K. Sectoral | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: sector_equities
- 1.L. Thematic | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: sector_equities
- 1.M. ELSS Tax Saver Fund | asset_class_sebi: equity_schemes | asset_class: equities | asset_subgroups: tax_efficient_equities

- 3.A. Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%) | asset_class_sebi: hybrid_schemes | asset_class: debt | asset_subgroups: Others
- 3.B. Balanced Hybrid Fund (Equity 40-60%, Debt 40-60%) | asset_class_sebi: hybrid_schemes | asset_class: debt | asset_subgroups: Others
- 3.C. Aggressive Hybrid Fund (Equity 65-80%, Debt 20-35%) | asset_class_sebi: hybrid_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 3.D. Dynamic Asset Allocation Fund | asset_class_sebi: hybrid_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 3.E. Multi-Asset Allocation Fund | asset_class_sebi: hybrid_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 3.F. Arbitrage Fund | asset_class_sebi: hybrid_schemes | asset_class: others | asset_subgroups: others

- 5.A. Index Funds & ETFs: | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: None
- 5.A.1 Large cap index linked | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: low_beta_equities
- 5.A.2 Multi cap index linked | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: medium_beta_equities
- 5.A.3 Sectoral & Thematic linked | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: sector_equities
- 5.A.4 Gold linked | asset_class_sebi: other_schemes | asset_class: others | asset_subgroups: gold_commodities
- 5.A.4 Silver linked | asset_class_sebi: other_schemes | asset_class: others | asset_subgroups: silver_commodities
- 5.A.4 Others | asset_class_sebi: other_schemes | asset_class: None | asset_subgroups: others

- 6.B.1 US linked | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: us_equities
- 6.B.2 China linked | asset_class_sebi: other_schemes | asset_class: equities | asset_subgroups: china_equities
- 6.B.3 Others | asset_class_sebi: other_schemes | asset_class: others | asset_subgroups: others_fofs

- 2.A. Overnight Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: near_debt
- 2.B. Liquid Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: near_debt
- 2.C. Ultra Short Fund (3–6 months) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: near_debt
- 2.D. Ultra Short to Short Term Fund (6–12 months) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: short_debt
- 2.E. Money market Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: short_debt
- 2.F. Short Term Fund (1–3 years) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: short_debt
- 2.G. Medium Term Fund (3–4 years) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: medium_debt
- 2.H. Medium Term to Long Term Fund (4-7 years) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: medium_debt
- 2.I. Long Term Fund (above 7 years) | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: long_duration_debt
- 2.J. Dynamic Term Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: medium_debt
- 2.K. Corporate Bond Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: high_risk_debt
- 2.L. Credit Risk Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: high_risk_debt
- 2.M. Banking and PSU Debt Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: high_risk_debt
- 2.N. Gilt Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: medium_debt
- 2.O. 10-year Constant Maturity Gilt Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: long_duration_debt
- 2.P. Floating Interest Rates Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: floating_debt
- 2.Q. Sectoral Fund | asset_class_sebi: debt_schemes | asset_class: debt | asset_subgroups: high_risk_debt

The below text provides the link of the portfolio allocation(Y) with the inputs(X). Broadly the inputs are investment_horizon, tax_planning, risk_capacity: low (1) to high (10)
Risk_willingness: low (1) to high (10), and the market_cycle
Treat these links as as theoritical foundation and heuristics. You need to link all the inidvidual inputs as provided by client and come out with the right allocation 


1) Emergency funds (investment_horizon): [Variables to check reglar expense]
  Each client must have 3 to 6 months of household expenses in Overnight Funds or Liquid Funds, with exact no of months within the range depending on factors like savings rate.
  If the Goal of the person is regular income from the portfolio with no other significant source of income to manage regular household expenses then additional 9 months of household expenses in: Ultra Short Fund (3–6 months) or Ultra Short to Short Term Fund (6–12 months) or Money market Fund 

  Why/ Rational:- Risk capacity is 1 
  1. To manage regular household expenses in case of any need and 2. To avoid the need to sell investments in case of any exigencies and market downturns, which can lead to locking in losses and derailing the long term goals of the person.

2) Short term funds (investment_horizon): [Variables to check regular one off expense or outflows for goal]
  If the client has a near term cash requirement for a one off expense or outflow for goal then such amount should be allocated in the following way:- 
  for expenses <12 months, then 100% amount should be allocated in Ultra Short Fund (3–6 months) or Ultra Short to Short Term Fund (6–12 months) or Money market Fund; 
  for expenses between 1-3 years, then at least 70% of the amount should be allocated in Short Term Fund  (1–3 years); balance allocation will depend on other factors as mentioned below such as age, occupation, ability & willingness to take risk, etc.

  Why/ Rational:- Why/ Rational:- Risk capacity is 2-3
  1. To ensure that the near term requirements are met without taking undue risk and 2. To avoid the need to sell investments during market downturns, which can lead to locking in losses and derailing thegoals of the person.

3) Tax saving (tax_planning): [Variables to check applicability of 80c deduction, marginal tax bracket] 
  If the client is under old tax regime and needs to save tax under 80C, then allocate up to Rs 150,000 in tax_efficient_equity schemes based on the risk capacity and return objective. 
  IMPORTANT!:If the client has an income above 1600000, to reduce tax impact od debt funds, instead of debt funds prefer to allocate to dynamic asset allocation and aggresive hybrid fund to the extent possible based on the risk capacity and risk willingness of the person and the guardrails as provided in other variables. For both these funds assumed 65% of the allocation as medium_beta_equities and 35% as medium_debt unless otherwise explicitly  mentioned for the specific mutual fund scheme. The allocation need to consider the weights assigned to medium_beta_equities and medium_debt for these funds while ensuring that the overall allocation to equities and debt is within the guardrails for the person based on their age and other factors.

4) Investment Horizon (investment_horizon): [Variables to check investment horizon for the goal, age]
Investment horizon means  the tenure the client plans to invest to achieve one or more goals.  
1. When the investment horizon is "long term" or "greater than 10 years" then allocation between assets is based on the age of the client along with his or her return objectives and risk tolerace.
2. When the Investment hirozon is "long term" for a client aged >60 years where the investment goal/purpose is for inter-generational wealth transfer, then the risk capacity increases to the age range of 45 with risk capacity rating of around 8.
3. When the investment horizon is "short term" or "less than 3 years" then allocation between assets is specified in the 2) short term funds.
4. When the investment horizon is "medium term" or "between 3 to 10 years" then allocation between assets is based on the following 
    (a) Allocation to Equities can be as per the formula below duly adjusted for the risk profile and return pbjective 
      H = investment horizon in years of the client, H_min = 3, H_max = 10
      Equity_min= 20%, Equity_max= 70%      
      Total equities = Equity_min + (Equity_max-Equity_min)* (H-H_min)/(H_max -H_min)
    (b) the allocation to Equities must not exceed the maximum equities allocation allowed as per the applicable_age of the client
    (c) the threshhold of subgroups of equities, debt and other allocation can be similar to the guardrails as provided for the age groups 50-55  with risk capacity of 5-6. The exact allocation will depend on the risk profile and return objectives. 

4) Risk capacity: The risk capacity needs to assesses based on age, income and expense, assets and liabilities

  Risk capacity
    
  Detailed Asset Class Allocation (Min% to Max%) based on risk_capacity score

    asset_class: equities
      - Score 10: Min 50%, Max 90%
      - Score 9.5: Min 50%, Max 90%
      - Score 9: Min 50%, Max 85%
      - Score 8.5: Min 50%, Max 80%
      - Score 8: Min 45%, Max 75%
      - Score 7.5: Min 40%, Max 70%
      - Score 7: Min 35%, Max 65%
      - Score 6: Min 30%, Max 60%
      - Score 5: Min 25%, Max 55%
      - Score 4: Min 20%, Max 50%
      - Score 3: Min 15%, Max 45%
      - Score 2.5: Min 10%, Max 40%
      - Score 2: Min 5%, Max 35%
      - Score 1.5: Min 5%, Max 30%
      - Score 1: Min 5%, Max 25%

    asset_class: equities, asset_subgroup: low_beta_equities
      - Score 10: Min 10%, Max 20%
      - Score 9.5: Min 10%, Max 20%
      - Score 9: Min 10%, Max 20%
      - Score 8.5: Min 10%, Max 20%
      - Score 8: Min 10%, Max 20%
      - Score 7.5: Min 5%, Max 20%
      - Score 7: Min 5%, Max 20%
      - Score 6: Min 5%, Max 20%
      - Score 5: Min 2.5%, Max 20%
      - Score 4: Min 2.5%, Max 15%
      - Score 3: Min 2.5%, Max 15%
      - Score 2.5: Min 0%, Max 15%
      - Score 2: Min 0%, Max 10%
      - Score 1.5: Min 0%, Max 10%
      - Score 1: Min 0%, Max 10%

    asset_class: equities, asset_subgroup:  value_equities
      - Score 10: Min 0%, Max 25%
      - Score 9.5: Min 0%, Max 25%
      - Score 9: Min 5%, Max 25%
      - Score 8.5: Min 5%, Max 25%
      - Score 8: Min 5%, Max 25%
      - Score 7.5: Min 5%, Max 25%
      - Score 7: Min 2.5%, Max 20%
      - Score 6: Min 2.5%, Max 20%
      - Score 5: Min 2.5%, Max 20%
      - Score 4: Min 2.5%, Max 15%
      - Score 3: Min 0%, Max 15%
      - Score 2.5: Min 0%, Max 15%
      - Score 2: Min 0%, Max 10%
      - Score 1.5: Min 0%, Max 10%
      - Score 1: Min 0%, Max 10%

    asset_class: equities, asset_subgroup: dividend_equities
      - Score 10: Min 0%, Max 20%
      - Score 9.5: Min 0%, Max 20%
      - Score 9: Min 0%, Max 20%
      - Score 8.5: Min 0%, Max 20%
      - Score 8: Min 0%, Max 20%
      - Score 7.5: Min 0%, Max 25%
      - Score 7: Min 0%, Max 25%
      - Score 6: Min 0%, Max 25%
      - Score 5: Min 0%, Max 20%
      - Score 4: Min 0%, Max 20%
      - Score 3: Min 0%, Max 20%
      - Score 2.5: Min 0%, Max 20%
      - Score 2: Min 0%, Max 20%
      - Score 1.5: Min 0%, Max 20%
      - Score 1: Min 0%, Max 15%

    asset_class: equities, asset_subgroup: medium_beta_equities
      - Score 10: Min 10%, Max 25%
      - Score 9.5: Min 10%, Max 25%
      - Score 9: Min 10%, Max 25%
      - Score 8.5: Min 10%, Max 25%
      - Score 8: Min 5%, Max 25%
      - Score 7.5: Min 5%, Max 20%
      - Score 7: Min 5%, Max 20%
      - Score 6: Min 5%, Max 20%
      - Score 5: Min 5%, Max 15%
      - Score 4: Min 2.5%, Max 15%
      - Score 3: Min 2.5%, Max 15%
      - Score 2.5: Min 2.5%, Max 15%
      - Score 2: Min 2.5%, Max 15%
      - Score 1.5: Min 2.5%, Max 15%
      - Score 1: Min 2.5%, Max 15%

    asset_class: equities, asset_subgroup:  high_beta_equities
      - Score 10: Min 10%, Max 25%
      - Score 9.5: Min 10%, Max 25%
      - Score 9: Min 10%, Max 25%
      - Score 8.5: Min 5%, Max 20%
      - Score 8: Min 5%, Max 20%
      - Score 7.5: Min 5%, Max 20%
      - Score 7: Min 5%, Max 20%
      - Score 6: Min 2.5%, Max 15%
      - Score 5: Min 0%, Max 15%
      - Score 4: Min 0%, Max 10%
      - Score 3: Min 0%, Max 10%
      - Score 2.5: Min 0%, Max 10%
      - Score 2: Min 0%, Max 5%
      - Score 1.5: Min 0%, Max 5%
      - Score 1: Min 0%, Max 5%

    asset_class: equities, asset_subgroup: sector_equities
      - Score 10: Min 0%, Max 10%
      - Score 9.5: Min 0%, Max 10%
      - Score 9: Min 0%, Max 10%
      - Score 8.5: Min 0%, Max 10%
      - Score 8: Min 0%, Max 10%
      - Score 7.5: Min 0%, Max 10%
      - Score 7: Min 0%, Max 10%
      - Score 6: Min 0%, Max 10%
      - Score 5: Min 0%, Max 10%
      - Score 4: Min 0%, Max 5%
      - Score 3: Min 0%, Max 5%
      - Score 2.5: Min 0%, Max 5%
      - Score 2: Min 0%, Max 5%
      - Score 1.5: Min 0%, Max 0%
      - Score 1: Min 0%, Max 0%

    asset_class: equities, asset_subgroup: us_equities
      - Score 10: Min 10%, Max 25%
      - Score 9.5: Min 10%, Max 25%
      - Score 9: Min 10%, Max 25%
      - Score 8.5: Min 10%, Max 20%
      - Score 8: Min 10%, Max 20%
      - Score 7.5: Min 10%, Max 20%
      - Score 7: Min 10%, Max 20%
      - Score 6: Min 5%, Max 20%
      - Score 5: Min 5%, Max 15%
      - Score 4: Min 5%, Max 15%
      - Score 3: Min 2.5%, Max 15%
      - Score 2.5: Min 2.5%, Max 10%
      - Score 2: Min 0%, Max 5%
      - Score 1.5: Min 0%, Max 5%
      - Score 1: Min 0%, Max 5%

    asset_class: debt
      - Score 10: Min 5%, Max 30%
      - Score 9.5: Min 5%, Max 30%
      - Score 9: Min 10%, Max 40%
      - Score 8.5: Min 15%, Max 45%
      - Score 8: Min 20%, Max 50%
      - Score 7.5: Min 25%, Max 55%
      - Score 7: Min 25%, Max 60%
      - Score 6: Min 30%, Max 65%
      - Score 5: Min 30%, Max 70%
      - Score 4: Min 35%, Max 75%
      - Score 3: Min 35%, Max 80%
      - Score 2.5: Min 40%, Max 85%
      - Score 2: Min 40%, Max 90%
      - Score 1.5: Min 40%, Max 95%
      - Score 1: Min 40%, Max 100%

    asset_class: debt, asset_subgroup: high_risk_debt
      - Score 10: Min 0%, Max 10%
      - Score 9.5: Min 0%, Max 10%
      - Score 9: Min 0%, Max 10%
      - Score 8.5: Min 0%, Max 10%
      - Score 8: Min 0%, Max 10%
      - Score 7.5: Min 0%, Max 10%
      - Score 7: Min 0%, Max 10%
      - Score 6: Min 0%, Max 10%
      - Score 5: Min 0%, Max 10%
      - Score 4: Min 0%, Max 10%
      - Score 3: Min 0%, Max 10%
      - Score 2.5: Min 0%, Max 10%
      - Score 2: Min 0%, Max 5%
      - Score 1.5: Min 0%, Max 5%
      - Score 1: Min 0%, Max 5%

    asset_class: debt, asset_subgroup: long_duration_debt
      - Score 10: Min 5%, Max 10%
      - Score 9.5: Min 5%, Max 10%
      - Score 9: Min 5%, Max 10%
      - Score 8.5: Min 5%, Max 15%
      - Score 8: Min 5%, Max 15%
      - Score 7.5: Min 5%, Max 20%
      - Score 7: Min 5%, Max 20%
      - Score 6: Min 5%, Max 20%
      - Score 5: Min 5%, Max 20%
      - Score 4: Min 5%, Max 20%
      - Score 3: Min 5%, Max 20%
      - Score 2.5: Min 5%, Max 20%
      - Score 2: Min 5%, Max 20%
      - Score 1.5: Min 5%, Max 20%
      - Score 1: Min 5%, Max 20%

    asset_class: debt, asset_subgroup: floating_debt
      - Score 10: Min 0%, Max 10%
      - Score 9.5: Min 0%, Max 10%
      - Score 9: Min 5%, Max 10%
      - Score 8.5: Min 5%, Max 15%
      - Score 8: Min 5%, Max 15%
      - Score 7.5: Min 10%, Max 20%
      - Score 7: Min 10%, Max 25%
      - Score 6: Min 10%, Max 25%
      - Score 5: Min 10%, Max 30%
      - Score 4: Min 15%, Max 30%
      - Score 3: Min 15%, Max 35%
      - Score 2.5: Min 20%, Max 40%
      - Score 2: Min 20%, Max 45%
      - Score 1.5: Min 20%, Max 50%
      - Score 1: Min 20%, Max 50%

    asset_class: debt, asset_subgroup: high_risk_debt
      - Score 10: Min 0%, Max 20%
      - Score 9.5: Min 0%, Max 20%
      - Score 9: Min 0%, Max 20%
      - Score 8.5: Min 5%, Max 20%
      - Score 8: Min 5%, Max 20%
      - Score 7.5: Min 5%, Max 20%
      - Score 7: Min 5%, Max 25%
      - Score 6: Min 10%, Max 25%
      - Score 5: Min 10%, Max 30%
      - Score 4: Min 10%, Max 30%
      - Score 3: Min 10%, Max 30%
      - Score 2.5: Min 10%, Max 30%
      - Score 2: Min 10%, Max 35%
      - Score 1.5: Min 10%, Max 35%
      - Score 1: Min 10%, Max 35%

    asset_class: debt, asset_subgroup: others
      - Score 10: Min 5%, Max 10%
      - Score 9.5: Min 5%, Max 10%
      - Score 9: Min 5%, Max 10%
      - Score 8.5: Min 5%, Max 10%
      - Score 8: Min 5%, Max 15%
      - Score 7.5: Min 5%, Max 15%
      - Score 7: Min 5%, Max 15%
      - Score 6: Min 5%, Max 15%
      - Score 5: Min 5%, Max 15%
      - Score 4: Min 5%, Max 15%
      - Score 3: Min 5%, Max 15%
      - Score 2.5: Min 5%, Max 10%
      - Score 2: Min 5%, Max 10%
      - Score 1.5: Min 5%, Max 10%
      - Score 1: Min 5%, Max 10%

    asset_class: others, asset_subgroup: gold_commodities
      - Score 10: Min 5%, Max 10%
      - Score 9.5: Min 5%, Max 10%
      - Score 9: Min 5%, Max 10%
      - Score 8.5: Min 5%, Max 10%
      - Score 8: Min 5%, Max 15%
      - Score 7.5: Min 5%, Max 15%
      - Score 7: Min 5%, Max 15%
      - Score 6: Min 5%, Max 15%
      - Score 5: Min 5%, Max 15%
      - Score 4: Min 5%, Max 15%
      - Score 3: Min 5%, Max 15%
      - Score 2.5: Min 5%, Max 10%
      - Score 2: Min 5%, Max 10%
      - Score 1.5: Min 5%, Max 10%
      - Score 1: Min 5%, Max 10%

    
Guardrail and interpolation rules
    The sum of all allocations across all schemes must equal 100%.
    The sum of allocations within an asset_subgroups must equal to the corresponding asset_class and You must not allocate below Min% or above Max% for any asset_class or asset_subgroup.
    For the investor’s effective risk_capacity, the total allocation to each group (equities, debt, others) must be between its Min% and Max%.
    For the investor’s effective risk_capacity, the total allocation to each subgroup must be between its Min% and Max%.
    If the investor’s risk_capacity lies between two defined Scores, linearly interpolate Min% and Max% between those ages separately for each asset_class and asset_subgroup.

5) Age (risk_capacity):  [Variables to check age of the person]

  When a client specifies an age, look up their risk_capacity score here:
- Age 20 -> risk_capacity: 10
- Age 25 -> risk_capacity: 9.5
- Age 30 -> risk_capacity: 9
- Age 35 -> risk_capacity: 8.5
- Age 40 -> risk_capacity: 8
- Age 45 -> risk_capacity: 7.5
- Age 50 -> risk_capacity: 7
- Age 55 -> risk_capacity: 6
- Age 60 -> risk_capacity: 5
- Age 65 -> risk_capacity: 4
- Age 70 -> risk_capacity: 3
- Age 75 -> risk_capacity: 2.5
- Age 80 -> risk_capacity: 2
- Age 85 -> risk_capacity: 1.5
- Age 90 -> risk_capacity: 1

  Age handling:
    If the investor’s age lies between two defined age, consider the age which is nearest to the investor's age to define the risk_olerance score. For example, an investor with age 23 will have the same risk_capacity score of age 25, an investor with age 31 will have the same risk_capacity score of age 30 
    If the investor’s age is below 20, use the 20‑year guardrails (do not go more aggressive).
    If the investor’s age is above 90, use the 90‑year guardrails (do not go more conservative or riskier than that grid).
    Apply this ratoinal consistently for each asset_class (total equities, debt, others) and every asset_subgroup listed above.

  Why/ Rational- Younger investors have large human capital (future earnings) and small financial capital, so they can hold more equities and withstand volatility

6.2) Assets and Liabilities (risk_capacity)

net_financial_assets =  financial_assets - liabilities excluding mortgage balance  
If net_financial_assets  < 0, then the amount of absolute net_financial_assets  need to be allocated in Ultra Short Fund (3–6 months) or Ultra Short to Short Term Fund (6–12 months) or Money market Fund.

expense_coverage_ratio =  financial_assets / (annual_expense + annual_mortgage_payment) 
expense_coverage_score  = 1, if expense_coverage_ratio <0.5
expense_coverage_score  = 10, if expense_coverage_ratio > 12  
Else, expense_coverage_score  = 1 + (expense_coverage_ratio - 0.5)/(12 - 0.5)


current_debt_percent = 100*(liabilities excluding mortgage + annual_mortgage_payment) / financial_assets
current_debt_score  = 1, if current_debt_percent > 100 (assuming covers total financial liabilities and 1 year mortgage payment)
current_debt_score  = 10, if current_debt_percent < 6 (assuming covers total financial liabilities and 12 year mortgage payment)
Else,  current_debt_score = 1 + (100 - current_debt_percent)/(100 - 6)

own_property_score = 10, if properties owned > 1
own_property_score = 8, if properties owned = 1
own_property_score = 2, if properties owned = 0

net_asset_score = 40% * expense_coverage_ratio + 30% * current_debt_percent + 30% * own_property_score 

The risk_capacity as provided in age will be adjusted as follows
  (1) risk_capacity_score = risk_capacity_score as per age + 50%*(net_asset_score - 5)
  (2) the risk_capacity_score must be in the range of 1 (lowest risk capacity) to 10 (highest risk capacity). Where the new scores are exceeding the maximum limit of range or reducing below the minimum limit of the range, then the score needs to be adjusted downwards to 10 and upwards to 1 respectively 
  (3) In cases where the score is revised downwards to maximum range of 10, then the allocation to equities should increase to reflect high risk_capacity within the range defined for the risk_capacity score.
  (4) In cases where the score is revised upwards to minimum range of 1, then the allocation to equities should reduce to reflect lower risk_capacity within the range defined for the risk_capacity score.
  

6.2) Income and Expenses (risk_capacity)
  Savings rate = (Annual Income - Annual Expense)/ Annual Income
  If the Investor has no source of regular income (when the person is retired or depends on investments as the primary source of income or is unemployed, student or homeowner), then savings rates is not relevant for the perosn and   asset allocation will depend on factors excluding savings rate.
  If the Investor's savings rate is <1%, then the investor must first create Emergency Funds at the upper end of range of 6 months. For the remaining funds, the allocation to equities will reduce, say by 10-20%, from the mean of the relevant range as per the age and risk profile.
  If the Investor's savings rate is >20%, then the allocation to equities asset_class and corresponding asset_subgroups will increase in the range 10%-30% proportional to the savings rate above 20% (for 10% increase) to above 70% (for 30% increase), from the mean of the relevant range as per the age, and applying the risk_capacity and risk_willingness constraints. 


6.3) Occupation
Occupation is a proxy for stability of human capital and is one of the factors to determine the risk_capacity_capacity of the client. 
Step 1: Define an Occupation Stability Index OSI based on the occupation as provided below:-
  Very stable occupation (e.g. tenured public sector, senior civil servant): OSI of 1 
  Typical white-collar employment : OSI ≈ 0.7
  Cyclical or commission-based (sales, trading): OSI ≈ 0.4
  Very volatile (startup founder with low salary, gig worker):  OSI ≈ 0.2 
  Stable long term family business : OSI  ≈ 0.6
  Unemployed (retired, sudent, homemaker) ≈ 0

  Step 2: Impact of allocation 
  If the person or the family has OSI ≈ 0, then the ris_capacity and allocation will depend on other factors as defiined. 
  High OSI means greater ability or capacity to take risks. However dont change the risk_capacity score based on this metric alone.
  Use the OSI as one of the factors along with income and expenses, assets and liabilities, to determine the exact % to be allocated to different asset_classes and asset_subgroups based on the risk_capacity score which is determined has been determined earlier.


7) Risk_willingess (risk_willingness)
    Each client's willingness to take risk is assessed in the range of 1 to 10: 1 = lowest risk_willingness and 10 =  highest risk_willingness;
    If the risk_willingness score is not equal to the risk_capacity score, then take the asset allocation range for age correspnding to the mean of risk_willingness score and risk_capacity score;
    In case the difference between the risk_willingness score and the risk_capacity score is more than 4, then allocation to equities and high_beta_equities should be conservative and generally less than the mid of the ranges of equities and high_beta_equities unless other variables make it otherwise; 
    The rules of 1) Emergency funds, 2) Short term funds, 3) Tax saving, 4) Investment Horizon, 6) Income and Expenses and 5) Market Impact will continue to apply 

    Why/ Rational: When an investor's willingness (emotional capacity) and ability (financial capacity) to take risk do not match, the general rule is to adopt the more conservative of the two to ensure the portfolio remains both financially viable and psychologically sustainable.
      If your willingness is high but your ability is low (e.g., a retiree with high spirit but low capital), you must invest conservatively. Overstepping your financial capacity risks "financial ruin" if market conditions turn adverse, regardless of your emotional courage.
      If your ability is high but your willingness is low (e.g., a young investor with high income but high anxiety), you should still lean toward a more conservative allocation. A mathematically "perfect" aggressive portfolio fails if it triggers panic selling during a downturn.
      

8)  Market Commentary (market_cycle) 
    Client recommended asset allocation will need to be adjusted based on
    (1) Our expressed views on different asset classes (on a scale from 1 - most negative  to 10 - most positive
    (2) The precise allocation number will lie within the allocation ranges (minimum and maximum) for each asset class and its subgroups corresponding to the risk_capacity score and risk_willinigness score, duly adjusted with other factors like time horizon.

    Step 1 — Interpret my views
    Use my table of “market_commerntary” (1 = most negative, 10 = most positive) for each asset class and its subgroups. Higher view scores should generally increase the target allocation within that asset class’s allowed range. Lower view scores should reduce allocation proportionately.
    Assets	Current view (1 - most negative to 10 - most positive)
    equities	3
    low_beta_equities	5
    value_equities	7
    dividend_equities	7
    medium_beta_equities	2
    high_beta_equities	1
    sector_equities	1
    us_equities	5
      
    debt	7
    high_risk_debt	3
    long_duration_debt	6
    floating_debt	8
    high_risk_debt	2
      
    others	
    gold_commodities	5

    Step 2 — Apply proportional scaling
    Adjust allocations proportionately;
    For each asset class and subgroup, map its view score (1–10) to its allocation range (Min–Max);
    Maintain the total portfolio allocation at 100% by proportionally scaling resulting values;
    If multiple subcategories exist (e.g., under “equities” or “debt”), ensure their sum does not exceed the parent category’s total range where applicable.

    After the table, explain briefly how the view scores affected the allocation (e.g., which assets were overweighted or underweighted relative to neutral expectations).
