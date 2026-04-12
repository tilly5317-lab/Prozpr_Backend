"""
ideal_portfolio: the optimised portfolio for a client based on his risk_capacity, risk_willingness and investment_horizon.
suggested_portfolio: the portfolio suggested based on the ideal_portfolio adjusted to reduce portfolio_churn, exit_load charges and tax_outgo
client_events: events which leads to rebalancing of clients portfolio
stcg_list: equity mutual funds where short term capital gains tax is applicable. Presently, for equity mutual funds held for less than 1 year 
illiquid_list_exit: funds within the  exit load period
securities_rankings - rank of funds assigned in the asset_class and asset_subgroups
material_changes means a change in a factor which is more than 10% of the range of the factor or which leads to more than 10% change in any output. 


Rebalancing :
1. client_events = Events or triggers when client_allocation updates are required  
  (1) cash_flow_triggers: when the client intends to contributes or withdraws cash
  (2) annual_update: Annually in the last quarter of financial year i.e. during the months of January, February and March 
  (3) periodic_updates: periodically (monthly, quarterly or semi annually) as required by the client or quarterly by default. This will exclude the annual_update.
  (4) life_update- when the client believes there is a material_event in life which would impact his or her portfolio allocation and thus clicks life_update or communicates life_update via the chat agent 
   
2. The percentage allocation in ideal_portfolio between different asset_classes and asset_subgroups should remain similar to the previous allocation unless there are changes to risk_capacity, risk_willingness, client_profile, or market_commentary. 
3. cash_flow_triggers
    (i) When the client contributes cash: 
      (A) First, when emergency_funds in actual_allocation <  emergency_funds in ideal_allocation, allocate money to the emergency_funds till the ideal_allocation
      (B) Then direct additional money  into underweight asset classes until weights of asset_class and asset_subgroups are back to the ideal_allocation.
      (C) When the weights are equal to ideal_allocation, then the remaining balance should be as per the ideal_allocation weights.

    (ii) When the client withdraws cash: Witdraw by using the funds held in following asset_class and asset_subgroups sequentially  
      (A) First sell  the emergency_funds. The maximum withdrawal amount from emergency_funds is capped at the  amount held in emergency funds
      (B) For the balance use funds in the short_term_funds and money_market_funds. The amount sold will be the lower of the remaining amount of withdrawal requested by the client amount post step A or the amount in short_term_funds and money_market_funds held.
      (C) For remaining amount, don't use securities held in stcg_list or illiquid_list_exit; Sell asset_class and asset_subgroups which have weights higher than the ideal_portfolio
      (D) For remaining amount, don't use securities held in stcg_list or illiquid_list_exit; Use other asset_class and asset_subgroups in the same  proportion as held in the portfolio.
      (D) For remaining amount, use securities in stcg_list, where the securities have loss_amount
      (E) Finally, use securities held in illiquid_list_exit and stucg_list proportionately
      .
4. annual_update [Will need to make workflow in front end]
    (A) First direct to allocation_skills with the updated client_profile, risk_capacity factors, risk_willingness factors, investment_horizon, market_commentary, and latest securities_rankings
      (i) Assess material_changes in the  client's risk_capacity, risk_willingnes and market_commentary.
      (ii) Small changes to be ignored but any material_changes in the factors will lead to change in allocation in the ideal_portfolio.
      (iii) Build the ideal_portfolio for the client as per the allocation_skills
    (B) Make changes to the ideal_portfolio to build the suggested_portfolio
      (i) Where secutrities need to be sold, dont sell quantity of securities in the stcg_list and the illiquid_list_exit.
      (ii) Where secutrities need to be replaced by another securities in the same asset_group, sell securities only when the difference_rankings is more than 2. 
      (iii) Where the difference_rankings between currently held securities and the newly recommended securities in the same asset_subgroup is less than / equal to 2 or is in stcg_list or  illiquid_list_exit, then hold thequantity of existing securites (capped at the maximum of the asset_subgroup). However, additional buy or new purchases for the asset_subgroup will only be of the securities as provided in ideal_allocation.
      (iv) Make changes to the ideal_allocation as mentioned aove and build a suggested_portfolio which is recommended to the clent.
    (C) The suggested_allocation will become the base. Recommend  sale of securities to bring actual_portfolio similar to suggested_allocation. Also recommend all new purchases to bring actual_portfolio similar to suggested_allocation but new buy securities will be only be of securities as per the updated ideal_allocation.
    
5.  periodic_updates
    (A) periodic_update of portfolio will only factor in changes in market_comentary and securities_rankings, keeping other variables similar. periodic_update will be treated as life_update if the client also updates client_profile, risk_capacity factors, risk_willingness factors, investment_horizon.
    (B) First direct to allocation_skills and check the difference in latest market_commentary and the previous market_commentary when the allocation was last updated.
      (i) market_commentary rating changes for asset_groups of less than and equal to 2 to be ignored in building the new ideal_portfolio.
      (ii) Build the ideal_portfolio for the client as per the allocation_skills only considering the changes in market_commentary ratings of more than 2, keeping other parameters and variables similar. 
    (B) Make changes to the ideal_portfolio to build the suggested_portfolio
      (i) Where secutrities need to be sold, dont sell quantity of securities in the stcg_list and the illiquid_list_exit.
      (ii) Where secutrities need to be replaced by another securities in the same asset_group, sell securities only when the difference_rankings is more than 2. 
      (iii) Where the difference_rankings between currently held securities and the newly recommended securities in the same asset_subgroup is less than / equal to 2 or is in stcg_list or  illiquid_list_exit, then hold the quantity of existing securites (capped at the maximum of the asset_subgroup). However, additional buy or new purchases for the asset_subgroup will only be of the securities as provided in ideal_allocation.
      (iv) Make changes to the ideal_allocation as mentioned aove and build a suggested_portfolio which is recommended to the clent
    (C) The suggested_allocation will become the base. Recommend  sale of securities to bring actual_portfolio similar to suggested_allocation. Also recommend all new purchases to bring actual_portfolio similar to suggested_allocation but new buy securities will be only be of securities as per the updated ideal_allocation.
        
6. life_updates      
    (A) life_update of portfolio will only factor in (i) changes in client_profile or any variables/parameters impacting risk_capacity and risk_willingness of client, (ii) market_comentary and (iii) securities_rankings, keeping other variables similar.
    (B) First direct to allocation_skills
      (i) Check the difference in latest market_commentary and the previous market_commentary when the allocation was last updated.
      (ii) market_commentary rating changes for asset_groups of less than and equal to 2 to be ignored in building the new ideal_portfolio.
      (iii) Check the impact of the change in client_profile on  risk_capacity factors, risk_willingness factors and investment_horizon,.
      (iv) Build the ideal_portfolio for the client as per the allocation_skills only considering the changes in market_commentary ratings of more than 2, and the impact of change in client_profile, keeping other parameters and variables similar. 
    (B) Make changes to the ideal_portfolio to build the suggested_portfolio
      (i) Where secutrities need to be sold, dont sell quantity of securities in the stcg_list and the illiquid_list_exit.
      (ii) Where secutrities need to be replaced by another securities in the same asset_group, sell securities only when the difference_rankings is more than 2. 
      (iii) Where the difference_rankings between currently held securities and the newly recommended securities in the same asset_subgroup is less than / equal to 2 or is in stcg_list or  illiquid_list_exit, then hold the quantity of existing securites (capped at the maximum of the asset_subgroup). However, additional buy or new purchases for the asset_subgroup will only be of the securities as provided in ideal_allocation.
      (iv) Make changes to the ideal_allocation as mentioned aove and build a suggested_portfolio which is recommended to the clent
    (C) The suggested_allocation will become the base. Recommend  sale of securities to bring actual_portfolio similar to suggested_allocation. Also recommend all new purchases to bring actual_portfolio similar to suggested_allocation but new buy securities will be only be of securities as per the updated ideal_allocation..
    
7.   human_loop: if the difference between the suggested_allocation and the previous suggested_allocation is significant [define?], then the new suggested_allocation need to be reviewd by a human.
    
 """
