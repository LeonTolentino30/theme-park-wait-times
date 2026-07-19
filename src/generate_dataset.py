import numpy as np
import pandas as pd

np.random.seed(42)

# Rides at Universal Orlando
rides = [
    "VelociCoaster",
    "Hagrid's Motorbike",
    "Hulk Coaster",
    "Spider-Man",
    "Forbidden Journey",
    "Revenge of the Mummy",
    "Transformers",
    "Rip Ride Rockit"
]

# Days of week
days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

def generate_wait_times(n=5000):
    data = []

    for _ in range(n):
        ride = np.random.choice(rides)
        day = np.random.choice(days)
        hour = np.random.randint(8, 23)  # park hours 8 AM–11 PM

        # Base wait time by ride popularity
        base = {
            "VelociCoaster": 55,
            "Hagrid's Motorbike": 65,
            "Hulk Coaster": 40,
            "Spider-Man": 35,
            "Forbidden Journey": 45,
            "Revenge of the Mummy": 30,
            "Transformers": 25,
            "Rip Ride Rockit": 30
        }[ride]

        # Weekend boost
        weekend = 20 if day in ["Sat","Sun"] else 0

        # Time-of-day pattern
        if 10 <= hour <= 15:
            tod = np.random.randint(15, 40)
        elif 16 <= hour <= 20:
            tod = np.random.randint(10, 25)
        else:
            tod = np.random.randint(0, 10)

        # Random noise
        noise = np.random.randint(-10, 15)

        wait_time = max(5, base + weekend + tod + noise)

        data.append([ride, day, hour, wait_time])

    df = pd.DataFrame(data, columns=["ride", "day", "hour", "wait_time"])
    return df

if __name__ == "__main__":
    df = generate_wait_times()
    df.to_csv("data/raw/wait_times.csv", index=False)
    print("Dataset generated: data/raw/wait_times.csv")