import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

# Load dataset
df = pd.read_csv("data/raw/wait_times.csv")

# One-hot encode categorical columns
df_encoded = pd.get_dummies(df, columns=["ride", "day"])

# Split features and target
X = df_encoded.drop("wait_time", axis=1)
y = df_encoded["wait_time"]

# Train/test split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

# Train model
model = RandomForestRegressor(n_estimators=200, random_state=42)
model.fit(X_train, y_train)

# Evaluate
preds = model.predict(X_test)
mae = mean_absolute_error(y_test, preds)

print(f"MAE: {mae:.2f} minutes")
