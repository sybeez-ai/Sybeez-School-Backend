-- Frozen Phase-1 schema (models at the initial revision, before 0002).
-- Generated from those models. Do not replace this with Base.metadata.create_all.
CREATE TABLE schools (
	name VARCHAR(255) NOT NULL, 
	slug VARCHAR(100) NOT NULL, 
	code VARCHAR(32), 
	email VARCHAR(320), 
	phone VARCHAR(32), 
	status VARCHAR(32) NOT NULL, 
	country VARCHAR(100), 
	city VARCHAR(100), 
	address TEXT, 
	timezone VARCHAR(64) NOT NULL, 
	logo_url TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_schools PRIMARY KEY (id), 
	CONSTRAINT uq_schools_code UNIQUE (code)
);
CREATE UNIQUE INDEX ix_schools_slug ON schools (slug);
CREATE TABLE users (
	email VARCHAR(320) NOT NULL, 
	password_hash VARCHAR(255), 
	first_name VARCHAR(100), 
	last_name VARCHAR(100), 
	full_name VARCHAR(200) NOT NULL, 
	phone VARCHAR(32), 
	status VARCHAR(32) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	is_platform_admin BOOLEAN NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	avatar_url TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE TABLE academic_years (
	school_id UUID NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	starts_on DATE NOT NULL, 
	ends_on DATE NOT NULL, 
	is_current BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_academic_years PRIMARY KEY (id), 
	CONSTRAINT uq_academic_year_school_name UNIQUE (school_id, name), 
	CONSTRAINT fk_academic_years_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_academic_years_school_id ON academic_years (school_id);
CREATE TABLE audit_logs (
	school_id UUID, 
	actor_id UUID, 
	action VARCHAR(128) NOT NULL, 
	resource_type VARCHAR(64), 
	resource_id VARCHAR(64), 
	ip_address VARCHAR(64), 
	details JSONB, 
	message TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
	CONSTRAINT fk_audit_logs_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE SET NULL, 
	CONSTRAINT fk_audit_logs_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_audit_logs_school_id ON audit_logs (school_id);
CREATE TABLE grades (
	school_id UUID NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	level INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_grades PRIMARY KEY (id), 
	CONSTRAINT uq_grade_school_name UNIQUE (school_id, name), 
	CONSTRAINT fk_grades_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_grades_school_id ON grades (school_id);
CREATE TABLE invitations (
	school_id UUID NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	token_hash VARCHAR(128) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	invited_by UUID, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	message TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_invitations PRIMARY KEY (id), 
	CONSTRAINT fk_invitations_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT uq_invitations_token_hash UNIQUE (token_hash), 
	CONSTRAINT fk_invitations_invited_by_users FOREIGN KEY(invited_by) REFERENCES users (id)
);
CREATE INDEX ix_invitations_email ON invitations (email);
CREATE INDEX ix_invitations_school_id ON invitations (school_id);
CREATE TABLE messages (
	school_id UUID NOT NULL, 
	sender_id UUID NOT NULL, 
	recipient_id UUID NOT NULL, 
	subject VARCHAR(255), 
	body TEXT NOT NULL, 
	is_read BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_messages PRIMARY KEY (id), 
	CONSTRAINT fk_messages_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_messages_sender_id_users FOREIGN KEY(sender_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_messages_recipient_id_users FOREIGN KEY(recipient_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_messages_recipient_id ON messages (recipient_id);
CREATE INDEX ix_messages_sender_id ON messages (sender_id);
CREATE INDEX ix_messages_school_id ON messages (school_id);
CREATE TABLE notifications (
	school_id UUID, 
	user_id UUID NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	is_read BOOLEAN NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_notifications PRIMARY KEY (id), 
	CONSTRAINT fk_notifications_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_notifications_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
CREATE INDEX ix_notifications_school_id ON notifications (school_id);
CREATE TABLE parents (
	school_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	phone VARCHAR(32), 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_parents PRIMARY KEY (id), 
	CONSTRAINT uq_parent_school_user UNIQUE (school_id, user_id), 
	CONSTRAINT fk_parents_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_parents_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_parents_school_id ON parents (school_id);
CREATE INDEX ix_parents_user_id ON parents (user_id);
CREATE TABLE refresh_tokens (
	user_id UUID NOT NULL, 
	jti VARCHAR(64) NOT NULL, 
	token_hash VARCHAR(128) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	replaced_by VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_refresh_tokens PRIMARY KEY (id), 
	CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);
CREATE UNIQUE INDEX ix_refresh_tokens_jti ON refresh_tokens (jti);
CREATE TABLE school_memberships (
	user_id UUID NOT NULL, 
	school_id UUID NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_school_memberships PRIMARY KEY (id), 
	CONSTRAINT uq_membership_user_school_role UNIQUE (user_id, school_id, role), 
	CONSTRAINT fk_school_memberships_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_school_memberships_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_school_memberships_school_id ON school_memberships (school_id);
CREATE TABLE security_events (
	school_id UUID, 
	user_id UUID, 
	event_type VARCHAR(64) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	ip_address VARCHAR(64), 
	details JSONB, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_security_events PRIMARY KEY (id), 
	CONSTRAINT fk_security_events_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE SET NULL, 
	CONSTRAINT fk_security_events_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_security_events_school_id ON security_events (school_id);
CREATE TABLE students (
	school_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	admission_no VARCHAR(64), 
	date_of_birth DATE, 
	gender VARCHAR(32), 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_students PRIMARY KEY (id), 
	CONSTRAINT uq_student_school_user UNIQUE (school_id, user_id), 
	CONSTRAINT fk_students_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_students_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_students_user_id ON students (user_id);
CREATE INDEX ix_students_school_id ON students (school_id);
CREATE TABLE subjects (
	school_id UUID NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	description VARCHAR(500), 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_subjects PRIMARY KEY (id), 
	CONSTRAINT uq_subject_school_code UNIQUE (school_id, code), 
	CONSTRAINT fk_subjects_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_subjects_school_id ON subjects (school_id);
CREATE TABLE subscriptions (
	school_id UUID NOT NULL, 
	plan_code VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_subscriptions PRIMARY KEY (id), 
	CONSTRAINT fk_subscriptions_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_subscriptions_school_id ON subscriptions (school_id);
CREATE TABLE teachers (
	school_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	employee_code VARCHAR(64), 
	title VARCHAR(64), 
	date_of_birth DATE, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_teachers PRIMARY KEY (id), 
	CONSTRAINT uq_teacher_school_user UNIQUE (school_id, user_id), 
	CONSTRAINT fk_teachers_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teachers_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_teachers_school_id ON teachers (school_id);
CREATE INDEX ix_teachers_user_id ON teachers (user_id);
CREATE TABLE achievements (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	badge_code VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_achievements PRIMARY KEY (id), 
	CONSTRAINT fk_achievements_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_achievements_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);
CREATE INDEX ix_achievements_school_id ON achievements (school_id);
CREATE INDEX ix_achievements_student_id ON achievements (student_id);
CREATE TABLE classes (
	school_id UUID NOT NULL, 
	grade_id UUID NOT NULL, 
	academic_year_id UUID NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	section VARCHAR(16), 
	capacity INTEGER, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_classes PRIMARY KEY (id), 
	CONSTRAINT uq_class_school_name_year UNIQUE (school_id, name, academic_year_id), 
	CONSTRAINT fk_classes_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_classes_grade_id_grades FOREIGN KEY(grade_id) REFERENCES grades (id) ON DELETE CASCADE, 
	CONSTRAINT fk_classes_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE
);
CREATE INDEX ix_classes_school_id ON classes (school_id);
CREATE INDEX ix_classes_grade_id ON classes (grade_id);
CREATE INDEX ix_classes_academic_year_id ON classes (academic_year_id);
CREATE TABLE student_ai_memory (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	memory_type VARCHAR(64) NOT NULL, 
	content TEXT NOT NULL, 
	confidence FLOAT, 
	metadata JSONB, 
	embedding VECTOR(1536), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_student_ai_memory PRIMARY KEY (id), 
	CONSTRAINT fk_student_ai_memory_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_ai_memory_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);
CREATE INDEX ix_student_ai_memory_school_id ON student_ai_memory (school_id);
CREATE INDEX ix_student_ai_memory_student_id ON student_ai_memory (student_id);
CREATE TABLE student_parents (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	parent_id UUID NOT NULL, 
	relationship VARCHAR(64) NOT NULL, 
	is_primary BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_student_parents PRIMARY KEY (id), 
	CONSTRAINT uq_student_parent UNIQUE (student_id, parent_id), 
	CONSTRAINT fk_student_parents_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_parents_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_parents_parent_id_parents FOREIGN KEY(parent_id) REFERENCES parents (id) ON DELETE CASCADE
);
CREATE INDEX ix_student_parents_student_id ON student_parents (student_id);
CREATE INDEX ix_student_parents_parent_id ON student_parents (parent_id);
CREATE INDEX ix_student_parents_school_id ON student_parents (school_id);
CREATE TABLE student_progress (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	academic_year_id UUID, 
	metric VARCHAR(64) NOT NULL, 
	value FLOAT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_student_progress PRIMARY KEY (id), 
	CONSTRAINT uq_student_progress_metric UNIQUE (student_id, subject_id, academic_year_id, metric), 
	CONSTRAINT fk_student_progress_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_progress_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_progress_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_student_progress_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE
);
CREATE INDEX ix_student_progress_student_id ON student_progress (student_id);
CREATE INDEX ix_student_progress_subject_id ON student_progress (subject_id);
CREATE INDEX ix_student_progress_academic_year_id ON student_progress (academic_year_id);
CREATE INDEX ix_student_progress_school_id ON student_progress (school_id);
CREATE TABLE syllabus (
	school_id UUID NOT NULL, 
	academic_year_id UUID NOT NULL, 
	grade_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_syllabus PRIMARY KEY (id), 
	CONSTRAINT uq_syllabus_year_grade_subject UNIQUE (school_id, academic_year_id, grade_id, subject_id), 
	CONSTRAINT fk_syllabus_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_syllabus_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_syllabus_grade_id_grades FOREIGN KEY(grade_id) REFERENCES grades (id) ON DELETE CASCADE, 
	CONSTRAINT fk_syllabus_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE
);
CREATE INDEX ix_syllabus_academic_year_id ON syllabus (academic_year_id);
CREATE INDEX ix_syllabus_grade_id ON syllabus (grade_id);
CREATE INDEX ix_syllabus_subject_id ON syllabus (subject_id);
CREATE INDEX ix_syllabus_school_id ON syllabus (school_id);
CREATE TABLE ai_conversations (
	school_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	student_id UUID, 
	subject_id UUID, 
	class_id UUID, 
	title VARCHAR(255), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_ai_conversations PRIMARY KEY (id), 
	CONSTRAINT fk_ai_conversations_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_ai_conversations_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_ai_conversations_student_id_students FOREIGN KEY(student_id) REFERENCES students (id), 
	CONSTRAINT fk_ai_conversations_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	CONSTRAINT fk_ai_conversations_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id)
);
CREATE INDEX ix_ai_conversations_school_id ON ai_conversations (school_id);
CREATE TABLE announcements (
	school_id UUID NOT NULL, 
	author_id UUID, 
	class_id UUID, 
	title VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	is_published BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_announcements PRIMARY KEY (id), 
	CONSTRAINT fk_announcements_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_announcements_author_id_users FOREIGN KEY(author_id) REFERENCES users (id) ON DELETE SET NULL, 
	CONSTRAINT fk_announcements_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE
);
CREATE INDEX ix_announcements_school_id ON announcements (school_id);
CREATE TABLE attendance (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	class_id UUID NOT NULL, 
	attended_on DATE NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	marked_by UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_attendance PRIMARY KEY (id), 
	CONSTRAINT uq_attendance_day UNIQUE (student_id, class_id, attended_on), 
	CONSTRAINT fk_attendance_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_attendance_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_attendance_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_attendance_marked_by_users FOREIGN KEY(marked_by) REFERENCES users (id)
);
CREATE INDEX ix_attendance_school_id ON attendance (school_id);
CREATE TABLE documents (
	school_id UUID NOT NULL, 
	uploaded_by UUID, 
	title VARCHAR(255) NOT NULL, 
	file_url TEXT NOT NULL, 
	mime_type VARCHAR(128), 
	class_id UUID, 
	subject_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_documents PRIMARY KEY (id), 
	CONSTRAINT fk_documents_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_documents_uploaded_by_users FOREIGN KEY(uploaded_by) REFERENCES users (id), 
	CONSTRAINT fk_documents_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id), 
	CONSTRAINT fk_documents_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id)
);
CREATE INDEX ix_documents_school_id ON documents (school_id);
CREATE TABLE enrollments (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	academic_year_id UUID NOT NULL, 
	grade_id UUID, 
	class_id UUID NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	enrolled_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_enrollments PRIMARY KEY (id), 
	CONSTRAINT uq_enrollment_student_year UNIQUE (student_id, academic_year_id), 
	CONSTRAINT fk_enrollments_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_enrollments_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_enrollments_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_enrollments_grade_id_grades FOREIGN KEY(grade_id) REFERENCES grades (id) ON DELETE SET NULL, 
	CONSTRAINT fk_enrollments_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE
);
CREATE INDEX ix_enrollments_academic_year_id ON enrollments (academic_year_id);
CREATE INDEX ix_enrollments_grade_id ON enrollments (grade_id);
CREATE INDEX ix_enrollments_class_id ON enrollments (class_id);
CREATE INDEX ix_enrollments_school_id ON enrollments (school_id);
CREATE INDEX ix_enrollments_student_id ON enrollments (student_id);
CREATE TABLE exams (
	school_id UUID NOT NULL, 
	academic_year_id UUID, 
	class_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	scheduled_at TIMESTAMP WITH TIME ZONE, 
	max_score FLOAT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_exams PRIMARY KEY (id), 
	CONSTRAINT fk_exams_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_exams_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_exams_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_exams_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE
);
CREATE INDEX ix_exams_class_id ON exams (class_id);
CREATE INDEX ix_exams_school_id ON exams (school_id);
CREATE INDEX ix_exams_academic_year_id ON exams (academic_year_id);
CREATE TABLE knowledge_documents (
	school_id UUID NOT NULL, 
	source_type VARCHAR(64) NOT NULL, 
	source_id UUID, 
	title VARCHAR(255) NOT NULL, 
	source_url TEXT, 
	subject_id UUID, 
	class_id UUID, 
	academic_year_id UUID, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_knowledge_documents PRIMARY KEY (id), 
	CONSTRAINT fk_knowledge_documents_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_knowledge_documents_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_documents_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_documents_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE SET NULL
);
CREATE INDEX ix_knowledge_documents_source_id ON knowledge_documents (source_id);
CREATE INDEX ix_knowledge_documents_school_id ON knowledge_documents (school_id);
CREATE TABLE syllabus_topics (
	syllabus_id UUID NOT NULL, 
	parent_topic_id UUID, 
	name VARCHAR(255) NOT NULL, 
	description TEXT, 
	order_index INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_syllabus_topics PRIMARY KEY (id), 
	CONSTRAINT fk_syllabus_topics_syllabus_id_syllabus FOREIGN KEY(syllabus_id) REFERENCES syllabus (id) ON DELETE CASCADE, 
	CONSTRAINT fk_syllabus_topics_parent_topic_id_syllabus_topics FOREIGN KEY(parent_topic_id) REFERENCES syllabus_topics (id) ON DELETE SET NULL
);
CREATE INDEX ix_syllabus_topics_syllabus_id ON syllabus_topics (syllabus_id);
CREATE INDEX ix_syllabus_topics_parent_topic_id ON syllabus_topics (parent_topic_id);
CREATE TABLE teacher_assignments (
	school_id UUID NOT NULL, 
	academic_year_id UUID, 
	teacher_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	class_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_teacher_assignments PRIMARY KEY (id), 
	CONSTRAINT uq_teacher_assignment UNIQUE (teacher_id, subject_id, class_id, academic_year_id), 
	CONSTRAINT fk_teacher_assignments_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teacher_assignments_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teacher_assignments_teacher_id_teachers FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teacher_assignments_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teacher_assignments_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE
);
CREATE INDEX ix_teacher_assignments_subject_id ON teacher_assignments (subject_id);
CREATE INDEX ix_teacher_assignments_academic_year_id ON teacher_assignments (academic_year_id);
CREATE INDEX ix_teacher_assignments_school_id ON teacher_assignments (school_id);
CREATE INDEX ix_teacher_assignments_teacher_id ON teacher_assignments (teacher_id);
CREATE INDEX ix_teacher_assignments_class_id ON teacher_assignments (class_id);
CREATE TABLE timetable_entries (
	school_id UUID NOT NULL, 
	academic_year_id UUID, 
	class_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	teacher_id UUID, 
	day_of_week INTEGER NOT NULL, 
	starts_at TIME WITHOUT TIME ZONE NOT NULL, 
	ends_at TIME WITHOUT TIME ZONE NOT NULL, 
	room VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_timetable_entries PRIMARY KEY (id), 
	CONSTRAINT fk_timetable_entries_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_timetable_entries_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_timetable_entries_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_timetable_entries_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_timetable_entries_teacher_id_teachers FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
);
CREATE INDEX ix_timetable_entries_school_id ON timetable_entries (school_id);
CREATE INDEX ix_timetable_entries_class_id ON timetable_entries (class_id);
CREATE INDEX ix_timetable_entries_academic_year_id ON timetable_entries (academic_year_id);
CREATE TABLE ai_messages (
	conversation_id UUID NOT NULL, 
	school_id UUID NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_ai_messages PRIMARY KEY (id), 
	CONSTRAINT fk_ai_messages_conversation_id_ai_conversations FOREIGN KEY(conversation_id) REFERENCES ai_conversations (id) ON DELETE CASCADE, 
	CONSTRAINT fk_ai_messages_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_ai_messages_school_id ON ai_messages (school_id);
CREATE INDEX ix_ai_messages_conversation_id ON ai_messages (conversation_id);
CREATE TABLE homework (
	school_id UUID NOT NULL, 
	class_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	teacher_id UUID NOT NULL, 
	syllabus_topic_id UUID, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	due_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_homework PRIMARY KEY (id), 
	CONSTRAINT fk_homework_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_teacher_id_teachers FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_syllabus_topic_id_syllabus_topics FOREIGN KEY(syllabus_topic_id) REFERENCES syllabus_topics (id) ON DELETE SET NULL
);
CREATE INDEX ix_homework_class_id ON homework (class_id);
CREATE INDEX ix_homework_school_id ON homework (school_id);
CREATE TABLE knowledge_chunks (
	document_id UUID NOT NULL, 
	school_id UUID NOT NULL, 
	subject_id UUID, 
	class_id UUID, 
	academic_year_id UUID, 
	content TEXT NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	metadata JSONB, 
	embedding VECTOR(1536), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id), 
	CONSTRAINT fk_knowledge_chunks_document_id_knowledge_documents FOREIGN KEY(document_id) REFERENCES knowledge_documents (id) ON DELETE CASCADE, 
	CONSTRAINT fk_knowledge_chunks_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_knowledge_chunks_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_chunks_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_chunks_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE SET NULL
);
CREATE INDEX ix_knowledge_chunks_document_id ON knowledge_chunks (document_id);
CREATE INDEX ix_knowledge_chunks_school_id ON knowledge_chunks (school_id);
CREATE TABLE questions (
	school_id UUID NOT NULL, 
	exam_id UUID NOT NULL, 
	prompt TEXT NOT NULL, 
	question_type VARCHAR(32) NOT NULL, 
	options JSONB, 
	answer_key TEXT, 
	points INTEGER NOT NULL, 
	sort_order INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_questions PRIMARY KEY (id), 
	CONSTRAINT fk_questions_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_questions_exam_id_exams FOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE CASCADE
);
CREATE INDEX ix_questions_school_id ON questions (school_id);
CREATE TABLE results (
	school_id UUID NOT NULL, 
	exam_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	score FLOAT NOT NULL, 
	percentage FLOAT, 
	grade_label VARCHAR(16), 
	remarks TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_results PRIMARY KEY (id), 
	CONSTRAINT uq_result_exam_student UNIQUE (exam_id, student_id), 
	CONSTRAINT fk_results_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_results_exam_id_exams FOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE CASCADE, 
	CONSTRAINT fk_results_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);
CREATE INDEX ix_results_exam_id ON results (exam_id);
CREATE INDEX ix_results_student_id ON results (student_id);
CREATE INDEX ix_results_school_id ON results (school_id);
CREATE TABLE teaching_records (
	school_id UUID NOT NULL, 
	academic_year_id UUID, 
	teacher_id UUID NOT NULL, 
	class_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	syllabus_topic_id UUID, 
	taught_on DATE NOT NULL, 
	topic VARCHAR(255) NOT NULL, 
	summary TEXT, 
	objectives TEXT, 
	homework_notes TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_teaching_records PRIMARY KEY (id), 
	CONSTRAINT fk_teaching_records_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teaching_records_academic_year_id_academic_years FOREIGN KEY(academic_year_id) REFERENCES academic_years (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teaching_records_teacher_id_teachers FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teaching_records_class_id_classes FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teaching_records_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_teaching_records_syllabus_topic_id_syllabus_topics FOREIGN KEY(syllabus_topic_id) REFERENCES syllabus_topics (id) ON DELETE SET NULL
);
CREATE INDEX ix_teaching_records_taught_on ON teaching_records (taught_on);
CREATE INDEX ix_teaching_records_academic_year_id ON teaching_records (academic_year_id);
CREATE INDEX ix_teaching_records_class_id ON teaching_records (class_id);
CREATE INDEX ix_teaching_records_school_id ON teaching_records (school_id);
CREATE INDEX ix_teaching_records_teacher_id ON teaching_records (teacher_id);
CREATE INDEX ix_teaching_records_subject_id ON teaching_records (subject_id);
CREATE TABLE homework_submissions (
	homework_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	school_id UUID NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE, 
	content TEXT, 
	status VARCHAR(32) NOT NULL, 
	marks FLOAT, 
	feedback TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_homework_submissions PRIMARY KEY (id), 
	CONSTRAINT fk_homework_submissions_homework_id_homework FOREIGN KEY(homework_id) REFERENCES homework (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_submissions_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_homework_submissions_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE
);
CREATE INDEX ix_homework_submissions_student_id ON homework_submissions (student_id);
CREATE INDEX ix_homework_submissions_homework_id ON homework_submissions (homework_id);
CREATE INDEX ix_homework_submissions_school_id ON homework_submissions (school_id);
CREATE TABLE learning_sessions (
	school_id UUID NOT NULL, 
	student_id UUID NOT NULL, 
	subject_id UUID, 
	teaching_record_id UUID, 
	mode VARCHAR(32) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	duration_seconds INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_learning_sessions PRIMARY KEY (id), 
	CONSTRAINT fk_learning_sessions_school_id_schools FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE, 
	CONSTRAINT fk_learning_sessions_student_id_students FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	CONSTRAINT fk_learning_sessions_subject_id_subjects FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	CONSTRAINT fk_learning_sessions_teaching_record_id_teaching_records FOREIGN KEY(teaching_record_id) REFERENCES teaching_records (id)
);
CREATE INDEX ix_learning_sessions_school_id ON learning_sessions (school_id);
