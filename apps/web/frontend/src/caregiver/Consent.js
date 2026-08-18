import React, { useState } from 'react';
import styles from '../patient/PatientConsent.module.css';
import { FaLock, FaFileAlt, FaShieldAlt } from 'react-icons/fa';
import { useNavigate } from "react-router-dom";

const Consent = () => {
  const navigate = useNavigate();
  const [consented, setConsented] = useState(false);

  const goHome = () => {
    // optional: show confirm dialog or save progress
    navigate("/");
  };

  const handleConsent = () => {
    if (!consented) {
      alert('Please review and accept the consent agreement');
      return;
    }
    navigate("/complete-profile");
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
          <div className={styles.iconWrapper2}>
            <FaShieldAlt className={styles.iconMain2} />
          </div>
          <h1 className={styles.title}>Caregiver Consent Agreement</h1>
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
                  <p className={styles.keyText}>Your access to MyCare user data is encrypted and access-controlled.</p>
                </div>
            </div>

            <div className={styles.keyPoint}>
                <div className={`${styles.iconBox} ${styles.purpleBox}`}>
                <FaFileAlt className={styles.purpleIcon} />
                </div>
                <div className={styles.textWrapper}>
                  <h4 className={styles.keyHeading}>Data Usage</h4>
                  <p className={styles.keyText}>Information you view or record as a caregiver is used only for caregiving coordination purposes.</p>
                </div>
            </div>

            <div className={styles.keyPoint}>
                <div className={`${styles.iconBox} ${styles.greenBox}`}>
                <FaShieldAlt className={styles.greenIcon} />
                </div>
                <div className={styles.textWrapper}>
                  <h3 className={styles.keyHeading}>Your Responsibilities</h3>
                  <p className={styles.keyText}>You must maintain confidentiality and use shared care information only to support the people who invited you.</p>
                </div>
            </div>
          </div>

          {/* Full Agreement */}
          <div className={styles.agreementBox}>
            <div className={styles.agreementHeader}>Full Agreement</div>
            <div className={styles.agreementScroll}>
              <h5>1. Introduction</h5>
              <p>
                This agreement outlines the terms for caregivers using RemiMinder’s caregiving organizer. By accepting, you acknowledge and agree to these terms.
              </p>

              <h5>2. Privacy</h5>
              <p>
                You must ensure all care information accessed through this platform remains confidential and secure.
              </p>

              <h5>3. Authorized Access</h5>
              <p>
                Caregivers are granted access only to MyCare users who have explicitly authorized sharing of their care information.
              </p>

              <h5>4. Data Handling</h5>
              <p>
                You may only access, record, or share care information as necessary to assist with caregiving coordination. Unauthorized disclosure is prohibited.
              </p>

              <h5>5. Confidentiality</h5>
              <p>
                You agree to protect all care information, maintain confidentiality, and prevent unauthorized viewing, copying, or distribution.
              </p>

              <h5>6. Liability and Limitations</h5>
              <p>
                RemiMinder provides caregiver access as a communication tool. It is not a substitute for advice from a care team.
              </p>

              <h5>7. Data Retention and Termination</h5>
              <p>
                Upon termination of your caregiver access or MyCare relationship, all associated data will be deleted or access revoked.
              </p>

              <h5>8. Amendments</h5>
              <p>
                RemiMinder may update these terms periodically. You will be notified and asked to re-consent if changes affect your role or responsibilities.
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
              I have read and agree to the terms outlined above. I understand my responsibilities as a caregiver under HIPAA and data privacy laws.
            </label>
          </div>

          {/* Button */}
          <button
            className={styles.submitButton2}
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

export default Consent;
