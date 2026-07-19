import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

df = pd.read_csv("data/raw/wait_times.csv")

# One-hot encode
df_encoded = pd.get_dummies(df, columns=["ride", "day"])

X = df_encoded.drop("wait_time", axis=1)
y = df_encoded["wait_time"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = RandomForestRegressor(n_estimators=200)
model.fit(X_train, y_train)

preds = model.predict(X_test)
mae = mean_absolute_error(y_test, preds)

print(f"MAE: {mae:.2f} minutes")

import joblib
joblib.dump(model, "models/wait_time_model.pkl")
print("Model saved to models/wait_time_model.pkl")
