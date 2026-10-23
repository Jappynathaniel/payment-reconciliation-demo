-- Generated from reconcile/db.py for PostgreSQL.

CREATE TABLE order_table (
	order_id VARCHAR(64) NOT NULL,
	merchant_reference VARCHAR(40) NOT NULL,
	merchant_id VARCHAR(64) NOT NULL,
	expo_id VARCHAR(64) NOT NULL,
	order_status VARCHAR(32) NOT NULL,
	amount_paise BIGINT NOT NULL,
	currency VARCHAR(3) NOT NULL,
	gateway_date VARCHAR(10) NOT NULL,
	gateway_payment_id VARCHAR(128),
	created_at BIGINT NOT NULL,
	confirmed_at BIGINT,
	next_check_at BIGINT NOT NULL,
	attempt_count INTEGER NOT NULL,
	lease_token VARCHAR(64),
	lease_until BIGINT,
	PRIMARY KEY (order_id),
	CHECK (amount_paise > 0),
	CHECK (currency = 'INR'),
	CHECK (order_status IN ('ORDER_PENDING','ORDER_PAID','ORDER_FAILED','REVIEW_REQUIRED')),
	UNIQUE (merchant_reference),
	UNIQUE (gateway_payment_id)
);

CREATE INDEX ix_due_orders ON order_table (order_status, next_check_at, created_at);

CREATE TABLE conference (
	conference_id VARCHAR(64) NOT NULL,
	expo_id VARCHAR(64) NOT NULL,
	conference_name VARCHAR(128) NOT NULL,
	PRIMARY KEY (conference_id)
);

CREATE TABLE visitor_table (
	visitor_id VARCHAR(64) NOT NULL,
	order_id VARCHAR(64) NOT NULL,
	conference_id VARCHAR(64) NOT NULL,
	expo_id VARCHAR(64) NOT NULL,
	visitor_data JSON NOT NULL,
	PRIMARY KEY (visitor_id),
	FOREIGN KEY(order_id) REFERENCES order_table (order_id),
	FOREIGN KEY(conference_id) REFERENCES conference (conference_id)
);

CREATE TABLE payment_checks (
	check_id VARCHAR(64) NOT NULL,
	order_id VARCHAR(64) NOT NULL,
	source VARCHAR(16) NOT NULL,
	outcome VARCHAR(64) NOT NULL,
	status_code VARCHAR(16),
	checked_at BIGINT NOT NULL,
	PRIMARY KEY (check_id),
	FOREIGN KEY(order_id) REFERENCES order_table (order_id)
);

CREATE INDEX ix_checks_order ON payment_checks (order_id, checked_at);

CREATE TABLE visitor_pass_table (
	pass_id VARCHAR(64) NOT NULL,
	visitor_id VARCHAR(64) NOT NULL,
	conference_id VARCHAR(64) NOT NULL,
	pass_data JSON NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (pass_id),
	CONSTRAINT uq_visitor_conference_pass UNIQUE (visitor_id, conference_id),
	FOREIGN KEY(visitor_id) REFERENCES visitor_table (visitor_id),
	FOREIGN KEY(conference_id) REFERENCES conference (conference_id)
);
