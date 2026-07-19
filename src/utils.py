import joblib
import pandas as pd

model = joblib.load("models/wait_time_model.pkl")

def predict_wait_time(ride, day, hour):
    df = pd.DataFrame([[ride, day, hour]], columns=["ride","day","hour"])
    df = pd.get_dummies(df)

    # Align columns with training data
    full_cols = joblib.load("models/training_columns.pkl")
    df = df.reindex(columns=full_cols, fill_value=0)

    return model.predict(df)[0]
