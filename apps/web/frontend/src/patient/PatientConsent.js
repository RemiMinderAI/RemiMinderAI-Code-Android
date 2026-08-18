import React, { useState } from 'react';
import styles from './PatientConsent.module.css';
import { FaLock, FaFileAlt, FaShieldAlt } from 'react-icons/fa';
import { useNavigate } from "react-router-dom";

const PatientConsent = () => {
  const navigate = useNavigate();

  const goHome = () => {
    // optional: show confirm dialog or save progress
    navigate("/");
  };
  
  const [consented, setConsented] = useState(false);

  const handleConsent = () => {
    if (!consented) {
      alert('Please review and accept the consent agreement');
      return;
    }
    // alert('Consent recorded successfully');
    navigate("/patient-profile-setup");
  };

  return (
    <div className={styles.container}>
      {/* Header (same as registration) */}
      <header className={styles.header}>
        <div className={styles.logo} onClick={goHome}>
          RemiMinderAI
        </div>
      </header>

      {/* Consent Section */}
      <main className={styles.main}>
        <div className={styles.pageHeader}>
          <div className={styles.iconWrapper}>
            <FaShieldAlt className={styles.iconMain} />
          </div>
          <h1 className={styles.title}>MyCare Consent Agreement</h1>
          <p className={styles.subtitle}>Please review the following information carefully</p>
        </div>
        
        <div className={styles.card}>
          {/* Key Points */}
          <div className={styles.keyPoints}>
            <div className={styles.keyPoint}>
                <div className={`${styles.iconBox} ${styles.blueBox}`}>
                <FaLock className={styles.blueIcon} />
                </div>
                <div className={styles.textWrapper}>
                  <h4 className={styles.keyHeading}>Privacy</h4>
                  <p className={styles.keyText}>Your care information is stored with encryption and access controls.</p>
                </div>
            </div>

            <div className={styles.keyPoint}>
                <div className={`${styles.iconBox} ${styles.purpleBox}`}>
                <FaFileAlt className={styles.purpleIcon} />
                </div>
                <div className={styles.textWrapper}>
                  <h4 className={styles.keyHeading}>Data Usage</h4>
                  <p className={styles.keyText}>Your visit recordings and summaries are used solely for your caregiving organization. You can delete your data at any time.</p>
                </div>
            </div>

            <div className={styles.keyPoint}>
                <div className={`${styles.iconBox} ${styles.greenBox}`}>
                <FaShieldAlt className={styles.greenIcon} />
                </div>
                <div className={styles.textWrapper}>
                  <h3 className={styles.keyHeading}>Your Rights</h3>
                  <p className={styles.keyText}>You have the right to access, modify, or delete your care information. You control who can access your data.</p>
                </div>
            </div>
            </div>

          {/* Full Agreement */}
          <div className={styles.agreementBox}>
            <div className={styles.agreementHeader}>Full Agreement</div>
            <div className={styles.agreementScroll}>
              <h5>1. Introduction</h5>
              <p>
                This agreement outlines the terms and conditions for using RemiMinder’s caregiving organizer. By accepting
                these terms, you acknowledge that you have read, understood, and agree to be bound by this agreement.
              </p>

              <h5>2. Privacy</h5>
              <p>
                RemiMinder is committed to protecting your care information. We implement appropriate safeguards to protect it
                from unauthorized access, use, or disclosure.
              </p>

              <h5>3. Data Collection and Use</h5>
              <p>We collect and process care information you provide, including visit recordings, voice data, and visit summaries. This information is used to:</p>
              <ul>
                <li>Generate AI-powered visit summaries</li>
                <li>Maintain your care records</li>
                <li>Facilitate communication with caregivers you authorize</li>
                <li>Improve our services while maintaining anonymization</li>
              </ul>

              <h5>4. Data Security</h5>
              <p>
                All data is encrypted in transit and at rest using industry-standard encryption protocols. We implement multi-factor authentication,
                regular security audits, and compliance monitoring to ensure data safety.
              </p>

              <h5>5. MyCare User Responsibilities</h5>
              <p>
                You are responsible for maintaining the confidentiality of your account, managing caregiver access,
                and ensuring the accuracy of information you provide.
              </p>

              <h5>6. Access and Control</h5>
              <p>
                You have the right to access your data, request corrections, download your records, revoke caregiver access,
                and request data deletion at any time.
              </p>

              <h5>7. Limitation of Liability</h5>
              <p>
                RemiMinder is a personal caregiving organizer. It is not a substitute for advice from your care team.
                AI-generated notes are informational only.
              </p>

              <h5>8. Prohibited Use</h5>
              <p>
                This platform is not intended for collecting personally identifiable information (PII) beyond what is necessary for caregiving coordination,
                nor for securing highly sensitive data requiring additional safeguards.
              </p>

              <h5>9. Termination</h5>
              <p>
                You may terminate your account at any time. Upon termination, your data will be permanently deleted within 30 days, unless retention is required by law.
              </p>

              <h5>10. Changes to Terms</h5>
              <p>
                We may update these terms periodically. You will be notified of significant changes and asked to re-consent.
              </p>
            </div>
          </div>

          {/* Checkbox */}
          <div className={styles.checkboxContainer}>
            <input
              type="checkbox"
              id="consent"
              checked={consented}
              onChange={(e) => setConsented(e.target.checked)}
            />
            <label htmlFor="consent">
              I have read and agree to the terms outlined above. I understand my rights and responsibilities,
              including how my care information will be used and protected.
            </label>
          </div>

          {/* Button */}
          <button
            className={styles.submitButton}
            onClick={handleConsent}
            disabled={!consented}
          >
            Accept & Continue
          </button>
        </div>

        <div className={styles.footerNote}>
          Questions about this agreement?{' '}
          <a href="#" className={styles.contactLink}>Contact Support</a>
        </div>
      </main>
    </div>
  );
};

export default PatientConsent;
