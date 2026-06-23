# ... [rest of your prompt text above]
It include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""  # <--- MAKE SURE THESE THREE QUOTES ARE HERE

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.0)
    print("\n=====================================================================")
    print(response.choices[0].message.content)
    print("=====================================================================")
except Exception as e: 
    print(f"❌ Layer 5 Summary Compiling Block Failure: {e}")
