#Track failed login attempts
from re import error
from flask import Flask, render_template, request, redirect, url_for, session, flash
import cv2
import numpy as np
import os
import logging
import random
import time
from twilio.rest import Client
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = "your_secret_key"

UPLOAD_FOLDER = "static/uploads/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# PostgreSQL Configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql:url"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Twiplio Configuration
TWILIO_ACCOUNT_SID = "id"
TWILIO_AUTH_TOKEN = "token"
TWILIO_PHONE_NUMBER = "phone number"
otp_storage = {}

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    aadhar = db.Column(db.String(12), unique=True, nullable=False)  #
    fingerprint_features = db.Column(db.PickleType, nullable=True)

# Initialize Database
with app.app_context():
    db.drop_all()
    db.create_all()

def match_fingerprints(registered_features, login_features):
    """Match fingerprints using Euclidean distance."""
    try:
        if registered_features is None or login_features is None:
            logging.warning("Missing fingerprint features for comparison")
            return False
        
        logging.debug("Calculating Euclidean distance for matching")
        #registered_features = np.array(session["registration_data"]["fingerprint_features"])  # Convert list back to NumPy array
        distance = np.linalg.norm(registered_features - login_features)
        return distance < 50  # Threshold value for matching
    except Exception as e:
        logging.error(f"Error in match_fingerprints: {str(e)}")
        return False


def send_otp(phone_number):
    """Send OTP using Twilio."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    otp = str(random.randint(100000, 999999))
    otp_storage[phone_number] = otp
    client.messages.create(
          body=f"Your OTP for fingerprint authentication is {otp}",
         from_=TWILIO_PHONE_NUMBER,
         to=phone_number,
     )
    print(otp)
    return otp

def process_fingerprint(image_path):
    """Enhance fingerprint image and extract minutiae."""
    try:
        logging.debug("Processing fingerprint image")
        original = cv2.imread(image_path)
        gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        blurred = cv2.GaussianBlur(enhanced, (3, 3), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        skeleton = cv2.ximgproc.thinning(binary)
        keypoints = extract_minutiae(skeleton)
# where is post deletion included in this project 
        os.remove(image_path)
        return keypoints
    except Exception as e:
        logging.error(f"Error in process_fingerprint: {str(e)}")
        return None

def extract_minutiae(skeleton):
    """Extract key minutiae features from the skeletonized fingerprint."""
    try:
        keypoints = cv2.goodFeaturesToTrack(skeleton.astype(np.uint8), 100, 0.01, 10)
        return keypoints
    except Exception as e:
        logging.error(f"Error in extract_minutiae: {str(e)}")
        return None

@app.route("/")
def index():
    return render_template("index.html")

# Track failed login attempts
failed_attempts = {}

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        step = request.form.get("step")

        if step == "upload":
            username = request.form.get("username")
            phone_number = request.form.get("phone")
            aadhar_number = request.form.get("aadhar")

            if not username or not phone_number or not aadhar_number:
                flash("Username, phone number, and Aadhar number are required.", "danger")
                return render_template("register.html", step="upload", error= "fill all the required fields")

            if User.query.filter_by(username=username).first():
                flash("Username already exists. Try a different one.", "danger")
                return render_template("register.html", step="upload", error="Username already exists. Try a different one.")

            if "file" not in request.files or request.files["file"].filename == "":
                flash("Please upload a fingerprint image.", "danger")
                return render_template("register.html", step="upload")

            file = request.files["file"]
            file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(file_path)

            if not check_liveness(file_path):
                flash("Fingerprint image appears to be fake or not live. Please try again.", "danger")
                return render_template("register.html", step="upload")

            minutiae_features = process_fingerprint(file_path)
            if minutiae_features is None:
                flash("Error processing fingerprint. Try again.", "danger")
                return render_template("register.html", step="upload")

            session["registration_data"] = {
                "username": username,
                "phone": phone_number,
                "aadhar": aadhar_number,
                "fingerprint_features": minutiae_features.tolist(),
            }

            send_otp(phone_number)
            logging.info(f"OTP sent to {phone_number} for verification.")

            return render_template("register.html", step="verify_otp", phone=phone_number)

        elif step == "verify_otp":
            otp = request.form.get("otp")
            reg_data = session.get("registration_data")

            if not reg_data:
                flash("Session expired. Please register again.", "danger")
                return render_template("register.html", step="upload")

            phone_number = reg_data["phone"]

            if otp_storage.get(phone_number) == otp:
                new_user = User(
                    username=reg_data["username"],
                    phone=reg_data["phone"],
                    aadhar=reg_data["aadhar"],  # Store Aadhar number as password
                    fingerprint_features=np.array(reg_data["fingerprint_features"]),
                )
                db.session.add(new_user)
                db.session.commit()

                otp_storage.pop(phone_number, None)
                session.pop("registration_data", None)

                logging.info("Registration successful! Please log in.")
                return render_template("register.html", step="success")
                

            else:
                flash("Invalid OTP. Please try again.", "danger")
                return render_template("register.html", step="verify_otp", phone=phone_number)

    return render_template("register.html", step="upload")

@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    phone = request.form.get("phone")
    entered_otp = request.form.get("otp")
    next_step = request.form.get("next_step")  # Expected value: "upload_fingerprint"
    username = session.get("login_user")

    if not username or not phone or not entered_otp or not next_step:
        return render_template("verify_otp.html", phone=phone, username=username, error="Invalid request. Try again.")

    # Verify OTP
    stored_otp = otp_storage.get(phone)
    if stored_otp and stored_otp == entered_otp:
        logging.info(f"OTP verification successful for {phone}")

        # Clear OTP after successful verification
        otp_storage.pop(phone, None)

        # Store verified user in session for fingerprint authentication
        session["username"] = username

        return redirect(url_for("upload_fingerprint"))

    else:
        logging.warning(f"Invalid OTP attempt for {phone}")
        return render_template("verify_otp.html", phone=phone, username=username, error="Invalid OTP. Try again.")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        aadhar_number = request.form.get("aadhar")

        if not username or not aadhar_number:
            return render_template("login.html", error="Please enter your username and Aadhar number.")

        # Check if user exists
        user = User.query.filter_by(username=username).first()
        if not user:
            return render_template("login.html", error="User not found. Please register first.")

        # Check if user is blocked
        if username in failed_attempts and failed_attempts[username]["blocked_until"] > time.time():
            return render_template("login.html", error="Too many failed attempts. Try again later.")

        # Check Aadhar number as password
        if aadhar_number != user.aadhar:
            if username not in failed_attempts:
                failed_attempts[username] = {"count": 1, "blocked_until": 0}
            else:
                failed_attempts[username]["count"] += 1

            if failed_attempts[username]["count"] >= 3:
                failed_attempts[username]["blocked_until"] = time.time() + 3600  # Block for 1 hour
                return render_template("login.html", error="Too many failed attempts. Try again in 1 hour go to home page.")

            return render_template("login.html", error=f"Incorrect Aadhar number. {3 - failed_attempts[username]['count']} attempts left.")

        # Reset failed attempts on successful login
        failed_attempts.pop(username, None)

        send_otp(user.phone)
        logging.info(f"OTP sent to {user.phone} for user {username}")

        session["login_user"] = username

        return render_template("verify_otp.html", phone=user.phone, username=username, next_step="upload_fingerprint")

    return render_template("login.html")


@app.route("/upload_fingerprint", methods=["GET", "POST"])
def upload_fingerprint():
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]
    user = User.query.filter_by(username=username).first()

    if request.method == "POST":
        if "file" not in request.files or request.files["file"].filename == "":
            return render_template("upload_fingerprint.html", error="Please upload a fingerprint image.")

        file = request.files["file"]
        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)

        uploaded_features = process_fingerprint(file_path)
        stored_features = user.fingerprint_features

        if match_fingerprints(uploaded_features, stored_features):
            return redirect(url_for("dashboard"))
        else:
            return render_template("upload_fingerprint.html", error="Fingerprint mismatch. Try again.")

    return render_template("upload_fingerprint.html")


@app.route("/dashboard")
def dashboard():
    if "username" in session:
        return render_template("dashboard.html", username=session["username"])
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("index"))

import cv2
import numpy as np

def check_liveness(image_path):
    """Check fingerprint liveness using image processing techniques."""
    try:
        # Load the fingerprint image in grayscale
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(image, (3, 3), 0)
        
        # Apply adaptive thresholding to enhance ridges
        binary = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 11, 2)

        # Edge detection using Canny
        edges = cv2.Canny(binary, 50, 150)

        # Count number of edges
        edge_count = np.sum(edges == 255)

        # Check if the fingerprint has enough complexity
        if edge_count < 5000:  # Threshold may need tuning
            return False  # Possible spoofed fingerprint (too smooth or fake)
        
        return True  # Likely a live fingerprint
    except Exception as e:
        print(f"Error in check_liveness: {str(e)}")
        return False

from tensorflow.keras.models import load_model
import cv2
import numpy as np

# Load trained liveness detection model
# model = load_model("liveness_detection_model2.h5")

def check_liveness_ml(image_path):
    """Check fingerprint liveness using a trained CNN model."""
    try:
        image = cv2.imread(image_path)
        image = cv2.resize(image, (224, 224))  # Resize for MobileNet
        image = image / 255.0  # Normalize
        image = np.expand_dims(image, axis=0)  # Add batch dimension

        prediction = model.predict(image)
        if prediction[0][0] > 0.5:
            return True  # Live fingerprint
        else:
            return False  # Fake fingerprint
    except Exception as e:
        print(f"Error in check_liveness_ml: {str(e)}")
        return False


if __name__ == "__main__":
    app.run(debug=True)
