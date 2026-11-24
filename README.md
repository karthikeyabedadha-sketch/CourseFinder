# CourseFinder 🎓
### Discover. Compare. Learn.

CourseFinder is a web-based course discovery platform designed to help learners find high-quality online courses across different fields of study.

The platform provides a curated collection of educational courses, allowing users to explore learning opportunities, compare course information, discover lesser-known resources, and make more informed decisions about their education.

Unlike traditional learning management systems, CourseFinder does not host lessons or provide its own certifications. Instead, it serves as a centralized discovery platform connecting learners with external educational resources.

---

## 1. Motivation

The internet offers thousands of online courses, but discovering the right one can be overwhelming.

Educational resources are scattered across different websites, and learners often struggle to identify courses that match their interests, experience levels, budgets, and learning goals.

I developed CourseFinder to address this problem by creating a centralized platform where learners can discover both popular and lesser-known educational opportunities.

My goal was to make finding relevant learning resources simpler, more organized, and more accessible.

---

## 2. Features

### Course Discovery
- Browse a curated collection of online courses.
- Explore courses across multiple subject categories.
- Discover both free and paid educational resources.
- Find lesser-known courses alongside established learning platforms.

### Search and Filtering
- Search for courses based on relevant keywords.
- Filter courses according to available categories and other course attributes.
- Sort and explore available learning opportunities.

### Course Information
- View individual course details.
- Explore course descriptions and relevant information.
- Access external course providers through their original links.
- Compare different learning opportunities before making a decision.

### User Accounts
- User registration and authentication.
- Secure login and logout functionality.
- User account management.
- Password recovery functionality.

### Reviews and Feedback
- Allow users to share feedback about courses.
- Provide learners with additional information when evaluating educational resources.

### Owner Studio
CourseFinder includes a dedicated management interface called Owner Studio.

It allows authorized administrators to manage the platform's course collection, including adding, editing, reviewing, and publishing course information.

The course management workflow supports different publication states, including draft, pending review, approved, and published.

Only published courses are intended to appear in the public course discovery experience.

---

## 3. Course Categories

CourseFinder is designed to cover a variety of educational fields, including:

- Computer Science
- Artificial Intelligence and Machine Learning
- Programming
- Data Science
- Finance and Investing
- Business and Entrepreneurship
- Marketing
- Design
- Mathematics
- Professional Skills

The platform is designed to accommodate additional categories as its collection grows.

---

## 4. Technologies Used

| Technology | Purpose |
|---|---|
| Python | Core application logic |
| Flask | Backend web framework |
| HTML | Page structure |
| CSS | Styling and layout |
| JavaScript | Frontend interactions |
| Supabase | Database and backend data storage |
| Gunicorn | Production application server |
| Git and GitHub | Version control and source code management |
| Pytest | Automated testing |

---

## 5. Application Architecture

CourseFinder follows a Flask-based application architecture.

The application separates its main responsibilities into different components:

- **Blueprints:** Handle different application routes and functionality.
- **Templates:** Render the user interface using HTML.
- **Forms:** Manage and validate user input.
- **Store layer:** Handles application data operations.
- **Static assets:** Contain frontend CSS, JavaScript, and other resources.
- **Supabase:** Stores application data and supports backend operations.

The application uses a data-store abstraction to separate data operations from the main application logic.

Supabase is used as the primary database backend.

---

## 6. Installation and Setup

Follow these steps to run CourseFinder locally.

### Prerequisites

- Python 3.11 or later.
- Git.
- A Supabase project with the required database schema.

### Step 1: Clone the repository

```bash
git clone https://github.com/karthikeyabedadha-sketch/CourseFinder.git
cd CourseFinder
```

### Step 2: Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows Git Bash:

```bash
source .venv/Scripts/activate
```

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure environment variables

Create a `.env` file using the provided `.env.example` as a reference.

```bash
cp .env.example .env
```

Configure the required environment variables using your own Supabase project credentials and application settings.

**Important:** Never publish your `.env` file, database credentials, or private API keys to GitHub.

### Step 5: Start the application

```bash
python wsgi.py
```

The application should be accessible locally at:

http://127.0.0.1:5000

---

## 7. Database

CourseFinder uses Supabase for persistent application data.

The database supports the platform's core operations, including course management, user-related data, and reviews.

The application is designed to retrieve publicly available courses based on their publication status, ensuring that unpublished courses are not displayed in the public discovery interface.

Database configuration is managed through environment variables rather than hardcoded credentials.

---

## 8. Testing

CourseFinder includes automated tests using Pytest.

To execute the test suite:

```bash
pytest -q
```

The project has passed 63 automated tests in the development environment at the time of the latest verification.

These tests cover application functionality and important backend workflows.

---

## 9. Project Structure

The application is organized into the following major directories:

```text
CourseFinder/
│
├── app/
│   ├── blueprints/
│   ├── static/
│   │   ├── css/
│   │   ├── js/
│   │   └── uploads/
│   ├── store/
│   ├── templates/
│   ├── forms.py
│   └── config.py
│
├── scripts/
│   └── migrate_sqlite_to_supabase.py
│
├── tests/
│
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── requirements.txt
├── wsgi.py
├── PROGRESS.md
└── README.md
```

---

## 10. Future Improvements

Some areas for future development include:

- Expanding the course collection.
- Improving personalized course recommendations.
- Introducing more advanced course comparison functionality.
- Enhancing the review and feedback system.
- Improving the platform's mobile experience.
- Expanding administrative analytics.
- Further improving search and discovery.

---

## 11. Author

**Karthikeya Bedadha**

CourseFinder was developed as my final project for Harvard University's CS50x — Introduction to Computer Science.

GitHub Repository: https://github.com/karthikeyabedadha-sketch/CourseFinder

---

## 12. Acknowledgements

- Harvard University — CS50x.
- Flask documentation.
- Supabase documentation.
- The open-source developer community.

